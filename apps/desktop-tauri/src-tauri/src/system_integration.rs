use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use tauri::AppHandle;
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

const MAX_NOTIFICATION_TITLE: usize = 128;
const MAX_NOTIFICATION_BODY: usize = 1_024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedNotification {
    pub title: String,
    pub body: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutostartStatus {
    pub enabled: bool,
}

pub fn prepare_notification(title: &str, body: &str) -> Result<PreparedNotification, String> {
    let title = title.trim();
    let body = body.trim();
    if title.is_empty() {
        return Err("notification title must not be empty".to_string());
    }
    if title.chars().count() > MAX_NOTIFICATION_TITLE {
        return Err(format!(
            "notification title exceeds the {MAX_NOTIFICATION_TITLE} character limit"
        ));
    }
    if body.chars().count() > MAX_NOTIFICATION_BODY {
        return Err(format!(
            "notification body exceeds the {MAX_NOTIFICATION_BODY} character limit"
        ));
    }
    Ok(PreparedNotification {
        title: title.to_string(),
        body: body.to_string(),
    })
}

pub fn resolve_open_path(raw_path: &str) -> Result<PathBuf, String> {
    let raw_path = raw_path.trim();
    if raw_path.is_empty() {
        return Err("open path must not be empty".to_string());
    }
    let path = PathBuf::from(raw_path);
    fs::canonicalize(&path)
        .map_err(|error| format!("path is unavailable ({}): {error}", path.display()))
}

pub fn set_autostart(app: &AppHandle, enabled: bool) -> Result<AutostartStatus, String> {
    let manager = app.autolaunch();
    if enabled {
        manager.enable().map_err(|error| error.to_string())?;
    } else {
        manager.disable().map_err(|error| error.to_string())?;
    }
    Ok(AutostartStatus {
        enabled: manager.is_enabled().map_err(|error| error.to_string())?,
    })
}

pub fn get_autostart(app: &AppHandle) -> Result<AutostartStatus, String> {
    Ok(AutostartStatus {
        enabled: app
            .autolaunch()
            .is_enabled()
            .map_err(|error| error.to_string())?,
    })
}

pub fn send_notification(app: &AppHandle, title: &str, body: &str) -> Result<(), String> {
    let notification = prepare_notification(title, body)?;
    app.notification()
        .builder()
        .title(notification.title)
        .body(notification.body)
        .show()
        .map_err(|error| error.to_string())
}

pub fn open_path(app: &AppHandle, raw_path: &str) -> Result<String, String> {
    let path = resolve_open_path(raw_path)?;
    app.opener()
        .open_path(path.to_string_lossy().into_owned(), None::<String>)
        .map_err(|error| error.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::{prepare_notification, resolve_open_path};
    use std::fs;

    #[test]
    fn notification_contract_trims_and_bounds_user_visible_text() {
        let notification =
            prepare_notification("  Task complete  ", "  The agent finished.  ").unwrap();

        assert_eq!(notification.title, "Task complete");
        assert_eq!(notification.body, "The agent finished.");
        assert!(prepare_notification("", "body").is_err());
        assert!(prepare_notification(&"x".repeat(129), "body").is_err());
        assert!(prepare_notification("title", &"x".repeat(1_025)).is_err());
    }

    #[test]
    fn opener_accepts_only_existing_files_or_directories() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("agent.log");
        fs::write(&file, "ready\n").unwrap();

        assert_eq!(
            resolve_open_path(file.to_str().unwrap()).unwrap(),
            fs::canonicalize(file).unwrap()
        );
        assert!(resolve_open_path(root.path().join("missing").to_str().unwrap()).is_err());
        assert!(resolve_open_path("").is_err());
    }
}
