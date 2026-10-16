//! T10b: the real generated ACL, exercised through Tauri's mock runtime.
//! The voice HUD window cannot call Context, credential, settings or other app
//! commands; the main window can.
use tauri::ipc::{CallbackFn, InvokeBody};
use tauri::test::{get_ipc_response, mock_builder, MockRuntime, INVOKE_KEY};
use tauri::webview::InvokeRequest;
use tauri::{Manager, WebviewWindow};
use vox_agent_desktop_tauri_lib::context_commands::{self, ContextState};

/// The app's own (local) origin: `http://tauri.localhost` on Windows,
/// `tauri://localhost` elsewhere.
#[cfg(windows)]
const LOCAL_ORIGIN: &str = "http://tauri.localhost";
#[cfg(not(windows))]
const LOCAL_ORIGIN: &str = "tauri://localhost";

fn app() -> tauri::App<MockRuntime> {
    mock_builder()
        .manage(ContextState::default())
        .invoke_handler(tauri::generate_handler![
            context_commands::context_quota,
            context_commands::context_list_records,
        ])
        .build(tauri::generate_context!())
        .unwrap()
}

fn window(app: &tauri::App<MockRuntime>, label: &str) -> WebviewWindow<MockRuntime> {
    app.get_webview_window(label).unwrap_or_else(|| {
        tauri::WebviewWindowBuilder::new(app, label, Default::default())
            .build()
            .unwrap()
    })
}

fn call(window: &WebviewWindow<MockRuntime>, cmd: &str) -> Result<String, String> {
    get_ipc_response(
        window,
        InvokeRequest {
            cmd: cmd.into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: LOCAL_ORIGIN.parse().unwrap(),
            body: InvokeBody::default(),
            headers: Default::default(),
            invoke_key: INVOKE_KEY.to_string(),
        },
    )
    .map(|body| format!("{body:?}"))
    .map_err(|error| error.to_string())
}

#[test]
fn the_voice_hud_cannot_call_app_commands_but_the_main_window_can() {
    let app = app();
    let hud = window(&app, "voice-hud");
    let main = window(&app, "main");
    for cmd in ["context_quota", "context_list_records"] {
        let refused = call(&hud, cmd).unwrap_err();
        assert!(refused.contains("not allowed"), "{cmd} from HUD: {refused}");
        // Allowed by the ACL; the unconfigured service answers its own error code.
        let answered = call(&main, cmd).unwrap_err();
        assert!(
            answered.contains("not-configured"),
            "{cmd} from main: {answered}"
        );
    }
}

#[test]
fn an_unknown_window_gets_nothing() {
    let app = app();
    let other = window(&app, "untrusted");
    assert!(call(&other, "context_quota")
        .unwrap_err()
        .contains("not allowed"));
}

#[test]
fn a_remote_origin_in_the_main_window_gets_nothing() {
    let app = app();
    let main = window(&app, "main");
    let refused = get_ipc_response(
        &main,
        InvokeRequest {
            cmd: "context_quota".into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: "https://evil.example".parse().unwrap(),
            body: InvokeBody::default(),
            headers: Default::default(),
            invoke_key: INVOKE_KEY.to_string(),
        },
    )
    .unwrap_err()
    .to_string();
    assert!(refused.contains("not allowed"), "{refused}");
}
