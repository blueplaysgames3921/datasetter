// src-tauri/src/commands.rs
//
// All Tauri commands callable from the frontend via invoke().
// Kept minimal — only things that genuinely need native OS access.

use tauri::{AppHandle, State};
use crate::SidecarState;

// ── Port ──────────────────────────────────────────────────────────────────────

/// Returns the port the Python sidecar is running on.
/// Frontend calls this once on startup, then uses it for all API calls.
#[tauri::command]
pub fn get_sidecar_port(state: State<SidecarState>) -> u16 {
    state.port
}

// ── Platform ──────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_platform() -> &'static str {
    if cfg!(target_os = "windows") { "windows" }
    else if cfg!(target_os = "macos") { "macos" }
    else if cfg!(target_os = "android") { "android" }
    else { "linux" }
}

// ── File dialogs ──────────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
pub struct FileDialogResult {
    pub paths: Vec<String>,
    pub cancelled: bool,
}

/// Open a native file picker. Returns selected file paths.
#[tauri::command]
pub async fn open_file_dialog(
    app: AppHandle,
    title: Option<String>,
    multiple: Option<bool>,
    filters: Option<Vec<FileFilter>>,
) -> FileDialogResult {
    use tauri_plugin_dialog::DialogExt;

    let mut dialog = app.dialog().file();

    if let Some(t) = title {
        dialog = dialog.set_title(&t);
    }

    if let Some(filters) = filters {
        for f in filters {
            let exts: Vec<&str> = f.extensions.iter().map(|s| s.as_str()).collect();
            dialog = dialog.add_filter(&f.name, &exts);
        }
    }

    let multi = multiple.unwrap_or(false);

    if multi {
        match dialog.blocking_pick_files() {
            Some(paths) => FileDialogResult {
                paths: paths.iter()
                    .filter_map(|p| p.to_str().map(String::from))
                    .collect(),
                cancelled: false,
            },
            None => FileDialogResult { paths: vec![], cancelled: true },
        }
    } else {
        match dialog.blocking_pick_file() {
            Some(path) => FileDialogResult {
                paths: path.to_str().map(|s| vec![s.to_string()]).unwrap_or_default(),
                cancelled: false,
            },
            None => FileDialogResult { paths: vec![], cancelled: true },
        }
    }
}

#[derive(serde::Deserialize)]
pub struct FileFilter {
    pub name: String,
    pub extensions: Vec<String>,
}

/// Open a native folder picker.
#[tauri::command]
pub async fn open_folder_dialog(
    app: AppHandle,
    title: Option<String>,
) -> FileDialogResult {
    use tauri_plugin_dialog::DialogExt;

    let mut dialog = app.dialog().file();

    if let Some(t) = title {
        dialog = dialog.set_title(&t);
    }

    match dialog.blocking_pick_folder() {
        Some(path) => FileDialogResult {
            paths: path.to_str().map(|s| vec![s.to_string()]).unwrap_or_default(),
            cancelled: false,
        },
        None => FileDialogResult { paths: vec![], cancelled: true },
    }
}

// ── Notifications ─────────────────────────────────────────────────────────────

/// Show a native OS notification.
#[tauri::command]
pub fn show_notification(
    app: AppHandle,
    title: String,
    body: String,
) -> Result<(), String> {
    use tauri_plugin_notification::NotificationExt;

    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .map_err(|e| e.to_string())
}

// ── Open path ─────────────────────────────────────────────────────────────────

/// Open a file or folder in the native OS file manager / default app.
#[tauri::command]
pub fn open_path(path: String) -> Result<(), String> {
    // Use the OS default handler
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}
