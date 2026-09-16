import Foundation

/// The exact environment the server process starts with. Nothing is inherited from the app.
public enum ServerEnvironment {
    public static func dataVariables(locations: AppLocations) -> [String: String] {
        ["CIRCUIT_MCP_DATA_DIR": locations.commandCenterDirectory.path,
         "CIRCUIT_MCP_SHOWMAN_DATA_DIR": locations.showmanDirectory.path,
         "CIRCUIT_MCP_WORKSPACE_CONFIG": locations.workspaceConfig.path]
    }

    public static func variables(locations: AppLocations, secrets: [String: String],
                                 home: String = NSHomeDirectory()) -> [String: String] {
        var variables = dataVariables(locations: locations)
        variables.merge(ocrVariables(locations: locations)) { existing, _ in existing }
        variables["HOME"] = home
        variables["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        variables["LANG"] = "en_US.UTF-8"
        variables["PYTHONDONTWRITEBYTECODE"] = "1"  // the bundle is read-only and already compiled
        variables.merge(secrets) { _, secret in secret }
        return variables
    }

    /// Where sub-project 5 will install the OCR environment and model. Neither path exists yet,
    /// and this task does not create them: OCR stays unavailable until that sub-project lands.
    ///
    /// Leaving the two variables unset would not leave it *honestly* unavailable. Unset,
    /// `paths.ocr_python()` and `paths.ocr_model()` fall back to `REPO_ROOT`, which inside the
    /// bundle resolves to `Resources/python/lib/python3.12` -- read-only paths no installer can
    /// ever fill, reported by `ocr_status` as the files the user should go and create. Naming the
    /// writable folder instead keeps that report true: `ocr_status` answers `ok: false` naming
    /// these paths, and a transcription call comes back reporting the same reason rather than
    /// failing somewhere deeper.
    private static func ocrVariables(locations: AppLocations) -> [String: String] {
        let ocr = locations.supportDirectory.appendingPathComponent("ocr", isDirectory: true)
        return ["CIRCUIT_MCP_OCR_PYTHON": ocr.appendingPathComponent("venv/bin/python").path,
                "CIRCUIT_MCP_OCR_MODEL": ocr.appendingPathComponent("models/unimernet_small").path]
    }
}
