use base64::Engine;
use reqwest::multipart::{Form, Part};
use reqwest::Client;
use serde::de::DeserializeOwned;
use serde::Serialize;
use thiserror::Error;

use crate::state::AppConfig;

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("request failed: {0}")]
    Request(#[from] reqwest::Error),
    #[error("invalid base64 audio payload: {0}")]
    Decode(#[from] base64::DecodeError),
}

fn client() -> Client {
    Client::new()
}

pub async fn get_json<T: DeserializeOwned>(
    config: &AppConfig,
    path: &str,
) -> Result<T, BridgeError> {
    let mut req = client().get(format!("{}{}", config.backend_base_url, path));
    if let Some(token) = &config.api_token {
        req = req.bearer_auth(token);
    }
    Ok(req.send().await?.error_for_status()?.json::<T>().await?)
}

pub async fn post_form<T: DeserializeOwned>(
    config: &AppConfig,
    path: &str,
    form: &[(&str, String)],
) -> Result<T, BridgeError> {
    let mut req = client()
        .post(format!("{}{}", config.backend_base_url, path))
        .form(form);
    if let Some(token) = &config.api_token {
        req = req.bearer_auth(token);
    }
    Ok(req.send().await?.error_for_status()?.json::<T>().await?)
}

pub async fn post_json<T: DeserializeOwned, B: Serialize>(
    config: &AppConfig,
    path: &str,
    body: &B,
) -> Result<T, BridgeError> {
    let mut req = client()
        .post(format!("{}{}", config.backend_base_url, path))
        .json(body);
    if let Some(token) = &config.api_token {
        req = req.bearer_auth(token);
    }
    Ok(req.send().await?.error_for_status()?.json::<T>().await?)
}

pub async fn post_voice_upload<T: DeserializeOwned>(
    config: &AppConfig,
    audio_base64: &str,
    mime_type: &str,
    source: &str,
    target_app: &str,
    session_id: &str,
    mode: &str,
) -> Result<T, BridgeError> {
    let audio = base64::engine::general_purpose::STANDARD.decode(audio_base64)?;

    let extension = match mime_type {
        "audio/webm" => "webm",
        "audio/ogg" => "ogg",
        "audio/wav" => "wav",
        _ => "bin",
    };

    let audio_part = Part::bytes(audio)
        .file_name(format!("utterance.{extension}"))
        .mime_str(mime_type)?;

    let form = Form::new()
        .part("audio", audio_part)
        .text("source", source.to_string())
        .text("target_app", target_app.to_string())
        .text("session_id", session_id.to_string())
        .text("mode", mode.to_string());

    let mut req = client()
        .post(format!("{}{}", config.backend_base_url, "/v1/voice"))
        .multipart(form);
    if let Some(token) = &config.api_token {
        req = req.bearer_auth(token);
    }
    Ok(req.send().await?.error_for_status()?.json::<T>().await?)
}
