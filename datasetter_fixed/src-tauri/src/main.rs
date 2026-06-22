// src-tauri/src/main.rs
//
// Tauri shell for Datasetter.
// Responsibilities (kept minimal — no complex logic here):
//   1. Spawn the Python sidecar (main.py via uvicorn) on startup
//   2. Kill it on app exit
//   3. Pick a free port and pass it to the sidecar via env var
//   4. Expose a small set of Tauri commands to the frontend:
//      - get_sidecar_port()   → frontend knows which port to call
//      - open_file_dialog()   → native file picker
//      - open_folder_dialog() → native folder picker
//      - show_notification()  → native OS notification
//      - get_platform()       → "windows" | "macos" | "linux" | "android"
//
// Everything else — AI, pipeline, storage — lives in the Python sidecar.
// Rust stays thin. Complex logic in Rust = bugs that are hard to trace.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;

use std::net::TcpListener;
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use tauri::{Manager, State};

// ── Sidecar state ─────────────────────────────────────────────────────────────

pub struct SidecarState {
    pub port:    u16,
    pub process: Arc<Mutex<Option<Child>>>,
}

// ── Port utilities ────────────────────────────────────────────────────────────

fn find_free_port() -> u16 {
    // Bind to port 0 → OS assigns a free port
    let listener = TcpListener::bind("127.0.0.1:0")
        .expect("Failed to bind to find a free port");
    listener.local_addr().unwrap().port()
}

// ── Sidecar launcher ──────────────────────────────────────────────────────────

fn spawn_sidecar(port: u16, app_handle: &tauri::AppHandle) -> Option<Child> {
    // Resolve path to the bundled Python sidecar
    // In dev: ../src-py/main.py
    // In prod: sidecar is bundled as a binary via tauri sidecar feature
    //          (python embeddable + our code packed via PyInstaller)

    let sidecar_path = if cfg!(debug_assertions) {
        // Development: run directly with python
        let resource_dir = app_handle.path().resource_dir()
            .expect("Could not resolve resource dir");
        resource_dir
            .parent().unwrap()   // out of src-tauri
            .join("src-py")
            .join("main.py")
    } else {
        // Production: bundled binary (built by PyInstaller)
        app_handle.path().resource_dir()
            .expect("Could not resolve resource dir")
            .join("sidecar")
            .join("main")   // main.exe on Windows, main on Unix
    };

    let python_bin = if cfg!(debug_assertions) {
        // Dev: use system python / venv python
        if cfg!(target_os = "windows") {
            "python".to_string()
        } else {
            "python3".to_string()
        }
    } else {
        // Prod: no separate python — PyInstaller binary is self-contained
        sidecar_path.to_string_lossy().to_string()
    };

    let mut cmd = if cfg!(debug_assertions) {
        let mut c = Command::new(&python_bin);
        c.arg(sidecar_path.to_str().unwrap());
        c
    } else {
        Command::new(&sidecar_path)
    };

    cmd.env("DATASETTER_PORT", port.to_string())
       .env("PYTHONUNBUFFERED", "1")
       .stdout(std::process::Stdio::piped())
       .stderr(std::process::Stdio::piped());

    // On Windows, suppress the console window for the sidecar process
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    match cmd.spawn() {
        Ok(child) => {
            println!("[tauri] Python sidecar spawned on port {port}");
            Some(child)
        }
        Err(e) => {
            eprintln!("[tauri] Failed to spawn sidecar: {e}");
            None
        }
    }
}

// ── Main ──────────────────────────────────────────────────────────────────────

fn main() {
    let port = find_free_port();

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState {
            port,
            process: Arc::new(Mutex::new(None)),
        })
        .setup(move |app| {
            let state: State<SidecarState> = app.state();
            let handle = app.handle().clone();

            // Spawn sidecar
            let child = spawn_sidecar(port, &handle);
            *state.process.lock().unwrap() = child;

            // Wait briefly for sidecar to be ready, then open the main window
            let handle2 = handle.clone();
            std::thread::spawn(move || {
                // Poll until sidecar responds to /health
                let client = reqwest::blocking::Client::new();
                let url     = format!("http://127.0.0.1:{port}/health");
                let mut ready = false;

                for _ in 0..30 {
                    std::thread::sleep(std::time::Duration::from_millis(300));
                    if let Ok(resp) = client.get(&url).send() {
                        if resp.status().is_success() {
                            ready = true;
                            break;
                        }
                    }
                }

                if ready {
                    println!("[tauri] Sidecar ready on port {port}");
                } else {
                    eprintln!("[tauri] Sidecar did not become ready in time");
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // Kill the sidecar when the last window closes
            if let tauri::WindowEvent::Destroyed = event {
                let state: State<SidecarState> = window.state();
                if let Ok(mut guard) = state.process.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                        println!("[tauri] Sidecar killed.");
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_sidecar_port,
            commands::open_file_dialog,
            commands::open_folder_dialog,
            commands::show_notification,
            commands::get_platform,
            commands::open_path,
        ])
        .run(tauri::generate_context!())
        .expect("Tauri application error");
}
