use serde::{Deserialize, Serialize};
use tauri::{Emitter, Manager, PhysicalPosition};

const HUD_LABEL: &str = "voice-hud";
const HUD_EVENT: &str = "vox-agent://voice-hud";

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VoiceHudUpdate {
    pub phase: String,
    pub target_agent: String,
    pub instruction: String,
    pub transcript: String,
    pub elapsed_ms: u64,
    pub rms_level: f32,
    pub peak_level: f32,
    pub visible: bool,
}

impl VoiceHudUpdate {
    fn normalize(mut self) -> Result<Self, String> {
        self.phase = self.phase.trim().to_ascii_lowercase();
        if !matches!(
            self.phase.as_str(),
            "preparing" | "listening" | "processing" | "sent" | "error" | "hidden"
        ) {
            return Err(format!("unsupported voice HUD phase: {}", self.phase));
        }
        self.target_agent = bounded_text(&self.target_agent, 128);
        if self.target_agent.is_empty() {
            self.target_agent = "Vox Agent Coordinator".to_string();
        }
        self.instruction = bounded_text(&self.instruction, 256);
        self.transcript = bounded_text(&self.transcript, 2_000);
        self.elapsed_ms = self.elapsed_ms.min(60 * 60 * 1_000);
        self.rms_level = bounded_level(self.rms_level);
        self.peak_level = bounded_level(self.peak_level).max(self.rms_level);
        if self.phase == "hidden" {
            self.visible = false;
        }
        Ok(self)
    }
}

fn bounded_text(value: &str, max_chars: usize) -> String {
    value.trim().chars().take(max_chars).collect()
}

fn bounded_level(value: f32) -> f32 {
    if value.is_finite() {
        value.clamp(0.0, 1.0)
    } else {
        0.0
    }
}

fn position_near_bottom(window: &tauri::WebviewWindow) -> Result<(), String> {
    let monitor = match window
        .current_monitor()
        .map_err(|error| error.to_string())?
    {
        Some(monitor) => monitor,
        None => window
            .primary_monitor()
            .map_err(|error| error.to_string())?
            .ok_or_else(|| "no monitor is available for the voice HUD".to_string())?,
    };
    let window_size = window.outer_size().map_err(|error| error.to_string())?;
    let monitor_size = monitor.size();
    let monitor_position = monitor.position();
    let x_offset = monitor_size.width.saturating_sub(window_size.width) / 2;
    let y_offset = monitor_size
        .height
        .saturating_sub(window_size.height)
        .saturating_sub(72);
    window
        .set_position(PhysicalPosition::new(
            monitor_position.x.saturating_add(x_offset as i32),
            monitor_position.y.saturating_add(y_offset as i32),
        ))
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn update_voice_hud(
    update: VoiceHudUpdate,
    app: tauri::AppHandle,
) -> Result<VoiceHudUpdate, String> {
    let update = update.normalize()?;
    let window = app
        .get_webview_window(HUD_LABEL)
        .ok_or_else(|| "voice HUD window is unavailable".to_string())?;

    if !update.visible {
        window.hide().map_err(|error| error.to_string())?;
        return Ok(update);
    }

    if !window.is_visible().map_err(|error| error.to_string())? {
        window
            .set_focusable(false)
            .map_err(|error| error.to_string())?;
        position_near_bottom(&window)?;
        window.show().map_err(|error| error.to_string())?;
    }
    app.emit_to(HUD_LABEL, HUD_EVENT, update.clone())
        .map_err(|error| error.to_string())?;
    Ok(update)
}

#[cfg(test)]
mod tests {
    use super::VoiceHudUpdate;

    fn update() -> VoiceHudUpdate {
        VoiceHudUpdate {
            phase: " LISTENING ".to_string(),
            target_agent: " Vox Agent Coordinator ".to_string(),
            instruction: "Release to send · Esc to cancel".to_string(),
            transcript: String::new(),
            elapsed_ms: 99_999_999,
            rms_level: f32::NAN,
            peak_level: 2.0,
            visible: true,
        }
    }

    #[test]
    fn normalizes_untrusted_hud_payloads() {
        let update = update().normalize().unwrap();

        assert_eq!(update.phase, "listening");
        assert_eq!(update.target_agent, "Vox Agent Coordinator");
        assert_eq!(update.elapsed_ms, 3_600_000);
        assert_eq!(update.rms_level, 0.0);
        assert_eq!(update.peak_level, 1.0);
    }

    #[test]
    fn rejects_unknown_phases_and_hidden_always_wins() {
        let mut invalid = update();
        invalid.phase = "thinking-ish".to_string();
        assert!(invalid.normalize().is_err());

        let mut hidden = update();
        hidden.phase = "hidden".to_string();
        assert!(!hidden.normalize().unwrap().visible);
    }
}
