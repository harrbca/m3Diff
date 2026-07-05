// m3diff desktop shell: spawns the Python engine as an NDJSON-over-stdio
// subprocess (`m3diff serve`), forwards each stdout line to the frontend as an
// `rpc://message` event, and exposes `rpc_send` to write a request line to the
// backend's stdin. ADR-001 transport, backend side.
//
// Dev spawns `python -m m3diff.cli serve` with PYTHONPATH -> engine/src (no
// install needed). Release will use a bundled PyInstaller sidecar (Phase 7).

use std::io::{BufRead, BufReader, Write};
use std::process::{ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use tauri::{Emitter, Manager};

struct Backend {
    // Dropping this on app exit closes the pipe; the backend reads EOF and exits.
    stdin: Mutex<ChildStdin>,
}

#[tauri::command]
fn rpc_send(state: tauri::State<Backend>, line: String) -> Result<(), String> {
    let mut stdin = state.stdin.lock().map_err(|e| e.to_string())?;
    writeln!(stdin, "{}", line).map_err(|e| e.to_string())?;
    stdin.flush().map_err(|e| e.to_string())?;
    Ok(())
}

// Writes engine-rendered export content (json/csv/md) to a user-chosen path.
// The path comes from the save dialog, so the user has explicitly picked it.
#[tauri::command]
fn save_text_file(path: String, contents: String) -> Result<(), String> {
    std::fs::write(&path, contents).map_err(|e| e.to_string())
}

fn spawn_backend(handle: tauri::AppHandle) -> std::io::Result<ChildStdin> {
    let python = std::env::var("M3DIFF_PYTHON").unwrap_or_else(|_| "python".to_string());
    let engine_src = std::env::var("M3DIFF_ENGINE_SRC")
        .unwrap_or_else(|_| concat!(env!("CARGO_MANIFEST_DIR"), "/../../engine/src").to_string());

    let mut child = Command::new(python)
        .args(["-m", "m3diff.cli", "serve"])
        .env("PYTHONPATH", engine_src)
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;

    let stdin = child.stdin.take().expect("child stdin");
    let stdout = child.stdout.take().expect("child stdout");

    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(line) => {
                    let _ = handle.emit("rpc://message", line);
                }
                Err(_) => break,
            }
        }
    });

    Ok(stdin)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let stdin = spawn_backend(app.handle().clone())?;
            app.manage(Backend {
                stdin: Mutex::new(stdin),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![rpc_send, save_text_file])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
