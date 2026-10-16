use base64::Engine;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Device, SampleFormat, Stream, StreamConfig};
use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use crate::capture_session::{CaptureHandle, CaptureOwner, CaptureSessions, SessionError};

const TARGET_SAMPLE_RATE: u32 = 16_000;
const MAX_CAPTURE_SECONDS: usize = 60 * 60;
const LEVEL_WINDOW_SAMPLES: usize = 2_048;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioInputDevice {
    pub name: String,
    pub is_default: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeCaptureStatus {
    pub recording: bool,
    /// The running session's number and owner (T26).
    pub session_id: Option<u64>,
    pub owner: Option<CaptureOwner>,
    /// Set when the previous session ended on its own (e.g. `device-lost`).
    pub ended_reason: Option<&'static str>,
    pub device_name: Option<String>,
    pub sample_rate: Option<u32>,
    pub channels: Option<u16>,
    pub elapsed_ms: u64,
    pub rms_level: f32,
    pub peak_level: f32,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeAudioResult {
    pub audio_base64: String,
    pub mime_type: String,
    pub duration_ms: u64,
    pub sample_rate: u32,
    pub channels: u16,
    pub device_name: String,
    pub byte_len: usize,
}

struct ActiveCapture {
    _stream: Stream,
    samples: Arc<Mutex<Vec<f32>>>,
    callback_error: Arc<Mutex<Option<String>>>,
    overflowed: Arc<AtomicBool>,
    started_at: Instant,
    device_name: String,
    sample_rate: u32,
    channels: u16,
}

impl CaptureHandle for ActiveCapture {
    fn failed(&self) -> bool {
        self.callback_error
            .lock()
            .map(|error| error.is_some())
            .unwrap_or(true)
    }

    fn discard(self) {
        // Stop the device first so no callback writes after the wipe.
        drop(self._stream);
        if let Ok(mut samples) = self.samples.lock() {
            samples.iter_mut().for_each(|sample| *sample = 0.0);
            samples.clear();
            samples.shrink_to_fit();
        }
    }
}

#[derive(Default)]
pub struct AudioCaptureState {
    sessions: Mutex<CaptureSessions<ActiveCapture>>,
}

impl AudioCaptureState {
    pub fn list_input_devices(&self) -> Result<Vec<AudioInputDevice>, String> {
        let host = cpal::default_host();
        let default_name = host.default_input_device().map(|device| device.to_string());
        let mut devices = host
            .input_devices()
            .map_err(|error| format!("failed to enumerate microphone devices: {error}"))?
            .map(|device| device.to_string())
            .map(|name| AudioInputDevice {
                is_default: default_name.as_deref() == Some(name.as_str()),
                name,
            })
            .collect::<Vec<_>>();
        devices.sort_by(|left, right| {
            right
                .is_default
                .cmp(&left.is_default)
                .then_with(|| left.name.cmp(&right.name))
        });
        Ok(devices)
    }

    pub fn status(&self) -> Result<NativeCaptureStatus, String> {
        let mut sessions = self
            .sessions
            .lock()
            .map_err(|_| "audio state lock failed".to_string())?;
        let lost = sessions.reap_failed();
        Ok(match sessions.current() {
            Some((id, owner, capture)) => {
                let samples = capture
                    .samples
                    .lock()
                    .map_err(|_| "audio sample lock failed".to_string())?;
                let (rms_level, peak_level) = measured_levels(&samples, LEVEL_WINDOW_SAMPLES);
                NativeCaptureStatus {
                    recording: true,
                    session_id: Some(id),
                    owner: Some(owner),
                    ended_reason: None,
                    device_name: Some(capture.device_name.clone()),
                    sample_rate: Some(capture.sample_rate),
                    channels: Some(capture.channels),
                    elapsed_ms: capture.started_at.elapsed().as_millis() as u64,
                    rms_level,
                    peak_level,
                }
            }
            None => NativeCaptureStatus {
                recording: false,
                session_id: None,
                owner: None,
                ended_reason: lost.then_some("device-lost"),
                device_name: None,
                sample_rate: None,
                channels: None,
                elapsed_ms: 0,
                rms_level: 0.0,
                peak_level: 0.0,
            },
        })
    }

    /// Start a capture owned by `owner`. Refused (`capture-busy:*`) while
    /// another session runs; the device is only opened when idle.
    pub fn start(
        &self,
        owner: CaptureOwner,
        requested_device: Option<String>,
    ) -> Result<NativeCaptureStatus, String> {
        let mut sessions = self
            .sessions
            .lock()
            .map_err(|_| "audio state lock failed".to_string())?;
        let id = sessions
            .begin(owner, || open_capture(requested_device))
            .map_err(|error| error.code())?;
        let (_, _, capture) = sessions
            .current()
            .ok_or_else(|| SessionError::DeviceLost.code())?;
        Ok(NativeCaptureStatus {
            recording: true,
            session_id: Some(id),
            owner: Some(owner),
            ended_reason: None,
            device_name: Some(capture.device_name.clone()),
            sample_rate: Some(capture.sample_rate),
            channels: Some(capture.channels),
            elapsed_ms: 0,
            rms_level: 0.0,
            peak_level: 0.0,
        })
    }

    /// Cancel session `session_id`: the stream stops and the audio is wiped
    /// here; nothing is returned to the WebView.
    pub fn cancel(&self, session_id: u64) -> Result<(), String> {
        self.sessions
            .lock()
            .map_err(|_| "audio state lock failed".to_string())?
            .cancel(session_id)
            .map_err(|error| error.code())
    }

    /// Finish session `session_id` and return its audio.
    pub fn stop(&self, session_id: u64) -> Result<NativeAudioResult, String> {
        let capture = self
            .sessions
            .lock()
            .map_err(|_| "audio state lock failed".to_string())?
            .finish(session_id)
            .map_err(|error| error.code())?;
        finalize(capture)
    }
}

fn open_capture(requested_device: Option<String>) -> Result<ActiveCapture, String> {
    let host = cpal::default_host();
    let requested = requested_device
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let device = match requested {
        Some(name) => host
            .input_devices()
            .map_err(|error| format!("failed to enumerate microphone devices: {error}"))?
            .find(|device| device.to_string() == name)
            .ok_or_else(|| format!("microphone device is unavailable: {name}"))?,
        None => host
            .default_input_device()
            .ok_or_else(|| "no default microphone device is available".to_string())?,
    };
    let device_name = device.to_string();
    let supported = device.default_input_config().map_err(|error| {
        format!("failed to read the microphone format for {device_name}: {error}")
    })?;
    let sample_format = supported.sample_format();
    let config = supported.config();
    let sample_rate = config.sample_rate;
    let channels = config.channels;
    if channels == 0 || sample_rate == 0 {
        return Err("microphone reported an invalid audio format".to_string());
    }

    let samples = Arc::new(Mutex::new(Vec::new()));
    let callback_error = Arc::new(Mutex::new(None));
    let overflowed = Arc::new(AtomicBool::new(false));
    let max_samples = sample_rate as usize * channels as usize * MAX_CAPTURE_SECONDS;
    let stream = build_input_stream(
        &device,
        config,
        sample_format,
        Arc::clone(&samples),
        Arc::clone(&callback_error),
        Arc::clone(&overflowed),
        max_samples,
    )?;
    stream
        .play()
        .map_err(|error| format!("failed to start microphone capture: {error}"))?;

    Ok(ActiveCapture {
        _stream: stream,
        samples,
        callback_error,
        overflowed,
        started_at: Instant::now(),
        device_name,
        sample_rate,
        channels,
    })
}

fn finalize(capture: ActiveCapture) -> Result<NativeAudioResult, String> {
    let ActiveCapture {
        _stream: stream,
        samples,
        callback_error,
        overflowed,
        started_at: _,
        device_name,
        sample_rate,
        channels,
    } = capture;
    drop(stream);

    if let Some(error) = callback_error
        .lock()
        .map_err(|_| "audio callback error lock failed".to_string())?
        .take()
    {
        return Err(format!("microphone capture failed: {error}"));
    }
    if overflowed.load(Ordering::Relaxed) {
        return Err("microphone capture exceeded the one-hour safety limit".to_string());
    }
    let captured = std::mem::take(
        &mut *samples
            .lock()
            .map_err(|_| "audio sample lock failed".to_string())?,
    );
    if captured.is_empty() {
        return Err("microphone capture produced no audio samples".to_string());
    }

    let input_frames = captured.len() / channels as usize;
    let duration_ms = ((input_frames as u128 * 1_000) / sample_rate as u128) as u64;
    let mono = downmix_and_resample(&captured, channels, sample_rate, TARGET_SAMPLE_RATE)?;
    let wav = encode_pcm16_wav(&mono, TARGET_SAMPLE_RATE);
    Ok(NativeAudioResult {
        audio_base64: base64::engine::general_purpose::STANDARD.encode(&wav),
        mime_type: "audio/wav".to_string(),
        duration_ms,
        sample_rate: TARGET_SAMPLE_RATE,
        channels: 1,
        device_name,
        byte_len: wav.len(),
    })
}

fn measured_levels(samples: &[f32], window_samples: usize) -> (f32, f32) {
    let window = &samples[samples.len().saturating_sub(window_samples.max(1))..];
    if window.is_empty() {
        return (0.0, 0.0);
    }

    let mut square_sum = 0.0_f64;
    let mut peak = 0.0_f32;
    let mut count = 0_u64;
    for sample in window.iter().copied().filter(|sample| sample.is_finite()) {
        let amplitude = sample.abs().clamp(0.0, 1.0);
        square_sum += f64::from(amplitude) * f64::from(amplitude);
        peak = peak.max(amplitude);
        count += 1;
    }
    if count == 0 {
        return (0.0, 0.0);
    }
    (
        ((square_sum / count as f64).sqrt() as f32).clamp(0.0, 1.0),
        peak,
    )
}

fn build_input_stream(
    device: &Device,
    config: StreamConfig,
    format: SampleFormat,
    samples: Arc<Mutex<Vec<f32>>>,
    callback_error: Arc<Mutex<Option<String>>>,
    overflowed: Arc<AtomicBool>,
    max_samples: usize,
) -> Result<Stream, String> {
    macro_rules! stream_for {
        ($sample:ty, $convert:expr) => {{
            let captured = Arc::clone(&samples);
            let overflow = Arc::clone(&overflowed);
            let errors = Arc::clone(&callback_error);
            device.build_input_stream(
                config,
                move |data: &[$sample], _| {
                    let Ok(mut output) = captured.lock() else {
                        return;
                    };
                    let remaining = max_samples.saturating_sub(output.len());
                    if data.len() > remaining {
                        overflow.store(true, Ordering::Relaxed);
                    }
                    output.extend(data.iter().take(remaining).map($convert));
                },
                move |error| {
                    if let Ok(mut target) = errors.lock() {
                        *target = Some(error.to_string());
                    }
                },
                None,
            )
        }};
    }

    let result = match format {
        SampleFormat::F32 => stream_for!(f32, |value: &f32| *value),
        SampleFormat::F64 => stream_for!(f64, |value: &f64| *value as f32),
        SampleFormat::I8 => stream_for!(i8, |value: &i8| *value as f32 / i8::MAX as f32),
        SampleFormat::I16 => stream_for!(i16, |value: &i16| *value as f32 / i16::MAX as f32),
        SampleFormat::I32 => stream_for!(i32, |value: &i32| *value as f32 / i32::MAX as f32),
        SampleFormat::I64 => stream_for!(i64, |value: &i64| *value as f32 / i64::MAX as f32),
        SampleFormat::U8 => stream_for!(u8, |value: &u8| (*value as f32 / u8::MAX as f32) * 2.0
            - 1.0),
        SampleFormat::U16 => stream_for!(u16, |value: &u16| (*value as f32 / u16::MAX as f32)
            * 2.0
            - 1.0),
        SampleFormat::U32 => stream_for!(u32, |value: &u32| (*value as f32 / u32::MAX as f32)
            * 2.0
            - 1.0),
        SampleFormat::U64 => stream_for!(u64, |value: &u64| (*value as f64 / u64::MAX as f64)
            as f32
            * 2.0
            - 1.0),
        unsupported => {
            return Err(format!(
                "unsupported microphone sample format: {unsupported:?}"
            ));
        }
    };
    result.map_err(|error| format!("failed to open microphone capture stream: {error}"))
}

fn downmix_and_resample(
    interleaved: &[f32],
    channels: u16,
    input_rate: u32,
    output_rate: u32,
) -> Result<Vec<f32>, String> {
    if channels == 0 || input_rate == 0 || output_rate == 0 {
        return Err("audio channels and sample rates must be non-zero".to_string());
    }
    let channel_count = channels as usize;
    let frames = interleaved.len() / channel_count;
    if frames == 0 {
        return Ok(Vec::new());
    }
    let mono = interleaved
        .chunks_exact(channel_count)
        .map(|frame| frame.iter().copied().sum::<f32>() / channel_count as f32)
        .collect::<Vec<_>>();
    if input_rate == output_rate {
        return Ok(mono);
    }

    let output_frames = ((frames as u128 * output_rate as u128) / input_rate as u128) as usize;
    let mut output = Vec::with_capacity(output_frames);
    for output_index in 0..output_frames {
        let source_position = output_index as f64 * input_rate as f64 / output_rate as f64;
        let left_index = source_position.floor() as usize;
        let right_index = (left_index + 1).min(mono.len() - 1);
        let fraction = (source_position - left_index as f64) as f32;
        output.push(mono[left_index] * (1.0 - fraction) + mono[right_index] * fraction);
    }
    Ok(output)
}

fn encode_pcm16_wav(samples: &[f32], sample_rate: u32) -> Vec<u8> {
    let data_len = samples.len().saturating_mul(2).min(u32::MAX as usize) as u32;
    let mut output = Vec::with_capacity(44 + data_len as usize);
    output.extend_from_slice(b"RIFF");
    output.extend_from_slice(&(36u32.saturating_add(data_len)).to_le_bytes());
    output.extend_from_slice(b"WAVEfmt ");
    output.extend_from_slice(&16u32.to_le_bytes());
    output.extend_from_slice(&1u16.to_le_bytes());
    output.extend_from_slice(&1u16.to_le_bytes());
    output.extend_from_slice(&sample_rate.to_le_bytes());
    output.extend_from_slice(&sample_rate.saturating_mul(2).to_le_bytes());
    output.extend_from_slice(&2u16.to_le_bytes());
    output.extend_from_slice(&16u16.to_le_bytes());
    output.extend_from_slice(b"data");
    output.extend_from_slice(&data_len.to_le_bytes());
    for sample in samples.iter().take(data_len as usize / 2) {
        let value = (sample.clamp(-1.0, 1.0) * i16::MAX as f32).round() as i16;
        output.extend_from_slice(&value.to_le_bytes());
    }
    output
}

#[cfg(test)]
mod tests {
    use super::{downmix_and_resample, encode_pcm16_wav, measured_levels};

    #[test]
    fn measures_rms_and_peak_from_only_the_recent_capture_window() {
        let (rms, peak) = measured_levels(&[1.0, 1.0, 0.5, -0.5], 2);

        assert!((rms - 0.5).abs() < 0.001);
        assert!((peak - 0.5).abs() < 0.001);
    }

    #[test]
    fn audio_levels_are_bounded_and_ignore_non_finite_samples() {
        let (rms, peak) = measured_levels(&[f32::NAN, f32::INFINITY, -2.0, 2.0], 8);

        assert!((rms - 1.0).abs() < 0.001);
        assert!((peak - 1.0).abs() < 0.001);
        assert_eq!(measured_levels(&[], 8), (0.0, 0.0));
    }

    #[test]
    fn downmixes_stereo_and_resamples_to_sixteen_kilohertz() {
        let stereo = vec![
            1.0, -1.0, // 0.0 mono
            0.5, 0.5, // 0.5 mono
            -0.5, -0.5, // -0.5 mono
            0.0, 1.0, // 0.5 mono
            1.0, 1.0, // 1.0 mono
            -1.0, -1.0, // -1.0 mono
        ];

        let output = downmix_and_resample(&stereo, 2, 48_000, 16_000).unwrap();

        assert_eq!(output.len(), 2);
        assert!((output[0] - 0.0).abs() < 0.001);
        assert!((output[1] - 0.5).abs() < 0.001);
    }

    #[test]
    fn wav_encoder_writes_pcm16_mono_header_and_clamps_samples() {
        let wav = encode_pcm16_wav(&[-2.0, 0.0, 2.0], 16_000);

        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(&wav[12..16], b"fmt ");
        assert_eq!(u16::from_le_bytes([wav[20], wav[21]]), 1);
        assert_eq!(u16::from_le_bytes([wav[22], wav[23]]), 1);
        assert_eq!(
            u32::from_le_bytes([wav[24], wav[25], wav[26], wav[27]]),
            16_000
        );
        assert_eq!(u16::from_le_bytes([wav[34], wav[35]]), 16);
        assert_eq!(&wav[36..40], b"data");
        assert_eq!(u32::from_le_bytes([wav[40], wav[41], wav[42], wav[43]]), 6);
        assert_eq!(i16::from_le_bytes([wav[44], wav[45]]), i16::MIN + 1);
        assert_eq!(i16::from_le_bytes([wav[46], wav[47]]), 0);
        assert_eq!(i16::from_le_bytes([wav[48], wav[49]]), i16::MAX);
    }

    #[test]
    fn rejects_invalid_audio_geometry() {
        assert!(downmix_and_resample(&[0.0], 0, 48_000, 16_000).is_err());
        assert!(downmix_and_resample(&[0.0], 1, 0, 16_000).is_err());
        assert!(downmix_and_resample(&[0.0], 1, 48_000, 0).is_err());
    }
}
