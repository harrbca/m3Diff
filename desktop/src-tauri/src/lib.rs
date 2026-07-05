// m3diff desktop shell: spawns the Python engine as an NDJSON-over-stdio
// subprocess (`m3diff serve`), forwards each stdout line to the frontend as an
// `rpc://message` event, and exposes `rpc_send` to write a request line to the
// backend's stdin. ADR-001 transport, backend side.
//
// Dev spawns `python -m m3diff.cli serve` with PYTHONPATH -> engine/src (no
// install needed). Release will use a bundled PyInstaller sidecar (Phase 7).

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use tauri::{Emitter, Manager};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

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

/// Where the shell writes its own log (spawn path, engine stderr, exit): the
/// same directory the engine logs into, so one folder tells the whole story.
fn shell_log_path() -> Option<PathBuf> {
    let base = std::env::var_os("APPDATA").map(PathBuf::from)?;
    let dir = base.join("m3diff").join("logs");
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join("shell.log"))
}

fn shell_log(line: &str) {
    if let Some(path) = shell_log_path() {
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(f, "[{now}] {line}");
        }
    }
}

/// The engine command: the bundled PyInstaller sidecar when present (release),
/// else `python -m m3diff.cli serve` from source (dev). M3DIFF_PYTHON forces
/// the dev path regardless.
fn engine_command() -> Command {
    let dev_python = std::env::var("M3DIFF_PYTHON").ok();
    if dev_python.is_none() {
        if let Ok(exe) = std::env::current_exe() {
            let sidecar = exe.with_file_name("m3diff-engine.exe");
            if sidecar.exists() {
                shell_log(&format!("spawning sidecar: {}", sidecar.display()));
                let mut cmd = Command::new(sidecar);
                cmd.arg("serve");
                return cmd;
            }
        }
    }
    let python = dev_python.unwrap_or_else(|| "python".to_string());
    let engine_src = std::env::var("M3DIFF_ENGINE_SRC")
        .unwrap_or_else(|_| concat!(env!("CARGO_MANIFEST_DIR"), "/../../engine/src").to_string());
    shell_log(&format!("spawning dev engine: {python} -m m3diff.cli serve (PYTHONPATH={engine_src})"));
    let mut cmd = Command::new(python);
    cmd.args(["-m", "m3diff.cli", "serve"])
        .env("PYTHONPATH", engine_src);
    cmd
}

fn spawn_backend(handle: tauri::AppHandle) -> std::io::Result<ChildStdin> {
    let mut command = engine_command();
    command
        .env("PYTHONUNBUFFERED", "1")
        // NDJSON transport is UTF-8; without this, Windows pipes default to the
        // locale codepage and real M3 data breaks the result frames. serve()
        // also reconfigures its stdio — this is defense in depth.
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        // Captured to shell.log: in a packaged GUI app "inherit" goes nowhere,
        // and stderr is where PyInstaller bootstrap errors and interpreter
        // panics land — exactly what post-mortems need.
        .stderr(Stdio::piped());

    // Console-subsystem sidecar under a GUI parent would flash a console
    // window; and per ADR-020, never share a console with the engine anyway.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = command.spawn()?;

    let stdin = child.stdin.take().expect("child stdin");
    let stdout = child.stdout.take().expect("child stdout");
    let stderr = child.stderr.take().expect("child stderr");

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
        // stdout closed = engine gone; note the exit for post-mortems.
        shell_log("engine stdout closed");
    });

    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            shell_log(&format!("engine stderr: {line}"));
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
