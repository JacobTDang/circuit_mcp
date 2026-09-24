import Foundation

/// The exact environment the server process starts with. Nothing is inherited from the app.
public enum ServerEnvironment {
    public static func dataVariables(locations: AppLocations) -> [String: String] {
        ["CIRCUIT_MCP_DATA_DIR": locations.commandCenterDirectory.path,
         "CIRCUIT_MCP_SHOWMAN_DATA_DIR": locations.showmanDirectory.path,
         "CIRCUIT_MCP_WORKSPACE_CONFIG": locations.workspaceConfig.path]
    }

    /// Every folder the server reads or writes and nothing secret: the whole environment an MCP
    /// client started from the generated config needs. The app's own server adds the process
    /// settings and the keys on top of it. A client given any less answers for the same install
    /// with bundle-internal paths the app never uses.
    static func clientVariables(locations: AppLocations, appBundle: URL? = nil) -> [String: String] {
        dataVariables(locations: locations)
            .merging(writableFolderVariables(locations: locations)) { existing, _ in existing }
            .merging(simulatorVariables(appBundle: appBundle)) { existing, _ in existing }
            .merging(receiverVariables(locations: locations, appBundle: appBundle)) { existing, _ in existing }
    }

    /// The AirPlay receiver the app carries, when it carries one.
    ///
    /// UxPlay is GStreamer, which finds its plugins through the environment rather than through
    /// its own load commands. Naming only the binary would leave it scanning whatever plugins the
    /// machine has -- none, on the Mac this app exists for -- so the plugin path and the scanner
    /// are named with it. The registry has to be somewhere writable: a .app is read-only once
    /// installed, and GStreamer writes the registry on first run.
    static func receiverVariables(locations: AppLocations, appBundle: URL?,
                                  fileManager: FileManager = .default) -> [String: String] {
        guard let appBundle else { return [:] }
        let binary = AppLocations.bundledUxplay(in: appBundle)
        guard fileManager.isExecutableFile(atPath: binary.path) else { return [:] }
        let staged = AppLocations.bundledGStreamer(in: appBundle)
        let plugins = staged.appendingPathComponent("plugins", isDirectory: true).path
        return [
            "CIRCUIT_MCP_UXPLAY": binary.path,
            "GST_PLUGIN_SYSTEM_PATH": plugins,
            "GST_PLUGIN_PATH": plugins,
            "GST_PLUGIN_SCANNER": staged.appendingPathComponent("libexec/gst-plugin-scanner").path,
            "GST_REGISTRY": locations.supportDirectory
                .appendingPathComponent("gstreamer-registry.bin").path,
        ]
    }

    /// The simulator the app carries, when it carries one.
    ///
    /// Unset, `paths.ngspice()` falls back to PATH, which is what a checkout wants. Set to a
    /// binary that is not there it would be worse than unset: `paths.ngspice()` refuses a
    /// variable it cannot run, so a bundle built without the ngspice stage would fail every
    /// simulation on a machine that had a working ngspice on PATH the whole time.
    static func simulatorVariables(appBundle: URL?,
                                   fileManager: FileManager = .default) -> [String: String] {
        guard let appBundle else { return [:] }
        let binary = AppLocations.bundledNgspice(in: appBundle)
        guard fileManager.isExecutableFile(atPath: binary.path) else { return [:] }
        return ["CIRCUIT_MCP_NGSPICE": binary.path]
    }

    public static func variables(locations: AppLocations, secrets: [String: String],
                                 home: String = NSHomeDirectory(),
                                 appBundle: URL? = nil) -> [String: String] {
        var variables = clientVariables(locations: locations, appBundle: appBundle)
        variables["HOME"] = home
        variables["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        variables["LANG"] = "en_US.UTF-8"
        variables["PYTHONDONTWRITEBYTECODE"] = "1"  // the bundle is read-only and already compiled
        variables.merge(secrets) { _, secret in secret }
        return variables
    }

    /// Where sub-projects 2-5 will install the runtime tools and the OCR environment and model.
    /// None of these paths exists yet, and this task does not create them: both features stay
    /// unavailable until those sub-projects land.
    ///
    /// Leaving the variables unset would not leave them *honestly* unavailable. Unset,
    /// `paths.ocr_python()`, `paths.ocr_model()` and `paths.runtime_dir()` fall back to
    /// `REPO_ROOT`, which inside the bundle resolves to `Resources/python/lib/python3.12` --
    /// read-only paths no installer can ever fill, reported by `ocr_status` as the files the user
    /// should go and create. The runtime folder is worse than a false report: `ipad_capture`
    /// *writes* `RUNTIME/ipad`, so unset it aims a write at the read-only bundle. Naming the
    /// writable folder instead keeps the report true: the status tools answer `ok: false` naming
    /// these paths, and a call comes back reporting the same reason rather than failing somewhere
    /// deeper.
    private static func writableFolderVariables(locations: AppLocations) -> [String: String] {
        let ocr = locations.supportDirectory.appendingPathComponent("ocr", isDirectory: true)
        return ["CIRCUIT_MCP_OCR_PYTHON": ocr.appendingPathComponent("venv/bin/python").path,
                "CIRCUIT_MCP_OCR_MODEL": ocr.appendingPathComponent("models/unimernet_small").path,
                "CIRCUIT_MCP_RUNTIME_DIR": locations.supportDirectory
                    .appendingPathComponent("runtime", isDirectory: true).path]
    }
}
