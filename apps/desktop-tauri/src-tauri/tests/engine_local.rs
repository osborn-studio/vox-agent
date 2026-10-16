//! T29b (local part): the shell trusts the listener on the engine port only
//! after it proves it holds this launch's secret, and never sends the secret
//! to a listener that cannot.
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::thread;

use vox_agent_desktop_tauri_lib::engine_local::{
    handshake_proof, verify_listener, HandshakeError, CHALLENGE_HEADER,
};

const TOKEN: &str = "launch-secret-0123456789abcdefghijklmnopqrstu";

/// A one-request server; `answer(challenge)` builds the reply body.
fn listener(
    status: &'static str,
    answer: impl Fn(&str) -> String + Send + 'static,
) -> (String, Arc<Mutex<String>>) {
    let socket = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", socket.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(String::new()));
    let record = seen.clone();
    thread::spawn(move || {
        if let Ok((mut stream, _)) = socket.accept() {
            let mut buffer = [0u8; 4096];
            let n = stream.read(&mut buffer).unwrap_or(0);
            let request = String::from_utf8_lossy(&buffer[..n]).to_string();
            let challenge = request
                .lines()
                .find_map(|l| {
                    l.split_once(':')
                        .filter(|(k, _)| k.eq_ignore_ascii_case(CHALLENGE_HEADER))
                        .map(|(_, v)| v.trim().to_string())
                })
                .unwrap_or_default();
            *record.lock().unwrap() = request;
            let body = answer(&challenge);
            let _ = write!(
                stream,
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
        }
    });
    (base, seen)
}

#[tokio::test(flavor = "multi_thread")]
async fn the_real_engine_answer_is_accepted_and_the_secret_is_not_sent() {
    let (base, seen) = listener("200 OK", |challenge| {
        format!(
            r#"{{"proof":"{}","version":"4.2.9"}}"#,
            handshake_proof(TOKEN, challenge)
        )
    });
    assert_eq!(verify_listener(&base, TOKEN).await, Ok("4.2.9".to_string()));
    let request = seen.lock().unwrap().clone();
    assert!(request.starts_with("GET /v1/local/handshake "));
    assert!(
        !request.contains(TOKEN),
        "the handshake never carries the secret"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn an_impostor_on_the_port_is_not_trusted() {
    // Any 200 JSON, a replayed proof for another challenge, or another secret.
    let answers: [fn(&str) -> String; 3] = [
        |_: &str| r#"{"status":"ok"}"#.to_string(),
        |_: &str| {
            format!(
                r#"{{"proof":"{}","version":"x"}}"#,
                handshake_proof(TOKEN, "00000000000000000000000000000000")
            )
        },
        |c: &str| {
            format!(
                r#"{{"proof":"{}","version":"x"}}"#,
                handshake_proof("other-secret", c)
            )
        },
    ];
    for answer in answers {
        let (base, _) = listener("200 OK", answer);
        assert_eq!(
            verify_listener(&base, TOKEN).await,
            Err(HandshakeError::IdentityUnverified)
        );
    }
    let (base, _) = listener("404 Not Found", |_| "{}".to_string());
    assert_eq!(
        verify_listener(&base, TOKEN).await,
        Err(HandshakeError::IdentityUnverified)
    );
    let (base, _) = listener("200 OK", |_| "x".repeat(10_000));
    assert_eq!(
        verify_listener(&base, TOKEN).await,
        Err(HandshakeError::IdentityUnverified)
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn nothing_listening_is_unreachable() {
    let socket = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", socket.local_addr().unwrap());
    drop(socket);
    assert_eq!(
        verify_listener(&base, TOKEN).await,
        Err(HandshakeError::Unreachable)
    );
}
