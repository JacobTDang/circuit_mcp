import AppKit
import PrepPalCore

final class AppDelegate: NSObject, NSApplicationDelegate {
    static let displayName = "Andrew's PrepPal"

    private var window: NSWindow!
    private let web = WebWindow()
    private let status = StatusView()
    private let secrets = SecretsStore()
    private var locations: AppLocations?
    /// All of the app's server bookkeeping: which controller it holds, what work is in flight,
    /// and whether a quit is waiting on it. Keeping none of it here is what keeps the rules in
    /// one testable place -- see `ServerLifecycle`.
    private let lifecycle = ServerLifecycle()
    private var logURL: URL?

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        if activateAnotherRunningCopy() {
            NSApp.terminate(nil)
            return
        }
        buildMenu()
        buildWindow()
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func activateAnotherRunningCopy() -> Bool {
        guard let identifier = Bundle.main.bundleIdentifier else { return false }
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
            .filter { $0 != NSRunningApplication.current }
        guard let other = others.first else { return false }
        other.activate()
        return true
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = Self.displayName
        window.setFrameAutosaveName("PrepPalMainWindow")
        // This delegate holds the only strong reference, so letting the close release it too
        // would over-release the window that the quit sequence still writes its status into.
        window.isReleasedWhenClosed = false

        let content = NSView()
        for view in [web.webView, status] as [NSView] {
            view.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(view)
            NSLayoutConstraint.activate([
                view.leadingAnchor.constraint(equalTo: content.leadingAnchor),
                view.trailingAnchor.constraint(equalTo: content.trailingAnchor),
                view.topAnchor.constraint(equalTo: content.topAnchor),
                view.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            ])
        }
        window.contentView = content

        status.onOpenLog = { [weak self] in self?.openLogs() }
        status.onRestart = { [weak self] in self?.restartServer() }
        status.onQuit = { NSApp.terminate(nil) }
        web.onLoadFailure = { [weak self] error in
            // Restarting the server for a page that failed to load costs the whole stop window,
            // so the cheap retry is named here: Reload Page leaves the server alone.
            self?.showFailure("The desk could not be loaded.", detail: """
                \(error.localizedDescription)

                Press Command-R to load the page again, or Restart to start the server again.
                """)
        }

        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    /// The folders, creating them if this is the first time they are needed.
    ///
    /// Every path that needs them goes through here rather than returning when there are none:
    /// the error screen's own Restart button leads back into this code, and a silent return left
    /// the user on a spinner with no buttons, no message, and no way out but Command-Q -- on the
    /// one screen whose whole purpose is recovery. Retrying is also what makes that button worth
    /// pressing: a folder that has become writable since launch is picked up here.
    private func requireLocations() -> AppLocations? {
        if let locations { return locations }
        do {
            let found = try AppLocations.current()
            try found.createDirectories()
            locations = found
            return found
        } catch {
            status.showError(title: "\(Self.displayName) cannot create its folders.", detail: "\(error)", logTail: nil)
            return nil
        }
    }

    // MARK: Server

    private func startServer() {
        guard let locations = requireLocations() else { return }
        if let refusal = lifecycle.refusalToStart {
            // Nothing the user can do reaches this. It is the last guard against a second server
            // that no reference would hold: one of those keeps running after the app is gone.
            NSLog("PrepPal did not start a server: %@", refusal)
            return
        }
        status.showLoading("Starting the server…")

        let python = AppLocations.bundledPython(in: Bundle.main.bundleURL)
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            status.showError(title: "\(Self.displayName) is damaged. Reinstall it.",
                             detail: "Missing or not executable: \(python.path)", logTail: nil)
            return
        }

        var serverSecrets: [String: String] = [:]
        var keychainProblem: String?
        do {
            serverSecrets = try secrets.serverSecrets()
        } catch {
            // The server still starts; Showman reports that no key is configured. The settings
            // sheet has to wait until it is running, because saving there restarts it: opening
            // it from here would start a second server on top of the one below.
            keychainProblem = "The Keychain could not be read, so the server started without your OpenRouter settings: \(error)"
        }

        let log: URL
        do {
            log = try LogFiles(directory: locations.logsDirectory).startNewLog()
        } catch {
            status.showError(title: "\(Self.displayName) cannot write its log.", detail: "\(error)", logTail: nil)
            return
        }
        logURL = log

        // The environment below carries the OpenRouter key. Neither it nor any value in it is
        // ever printed, logged, or written to the server log: only names would be, and
        // `ServerConfiguration` redacts the values even if one is interpolated by accident.
        let controller = ServerController(configuration: ServerConfiguration(
            executable: python,
            arguments: ["-m", "circuit_mcp.app_server", "--data-dir", locations.commandCenterDirectory.path],
            environment: ServerEnvironment.variables(locations: locations, secrets: serverSecrets),
            logFile: log
        ))
        controller.onExit = { [weak self] exitStatus in
            guard self?.lifecycle.liveServer === controller else { return }
            self?.showFailure("The server stopped unexpectedly (exit status \(exitStatus)).", detail: "Restart to start it again.")
        }
        if let refusal = lifecycle.adopt(controller) {
            NSLog("PrepPal did not start a server: %@", refusal)
            return
        }
        controller.start { [weak self] event in self?.handle(event, from: controller) }

        if let keychainProblem { showSettings(reason: keychainProblem) }
    }

    private func handle(_ event: ServerEvent, from controller: ServerController) {
        // A restart or an import replaces the controller; a late event from the old one would
        // otherwise put its failure screen over the server that is running now.
        guard lifecycle.liveServer === controller else { return }
        switch event {
        case .ready(let port):
            status.showLoading("Waiting for the server to answer…")
            Task { @MainActor [weak self] in
                do {
                    try await HealthCheck().waitUntilHealthy(port: port)
                    guard let self, self.lifecycle.liveServer === controller else { return }
                    self.web.load(port: port)
                    self.status.hide()
                } catch {
                    guard let self, self.lifecycle.liveServer === controller else { return }
                    self.showFailure("The server did not answer on port \(port).", detail: "\(error)")
                }
            }
        case .failed(let failure):
            showFailure(Self.headline(for: failure), detail: "\(failure)")
        }
    }

    static func headline(for failure: ServerFailure) -> String {
        switch failure {
        case .launchFailed: return "\(displayName) could not start its server."
        case .locked(let pid): return "Another \(displayName) server is using this data folder (process \(pid))."
        case .malformedLine: return "The server sent a startup message the app could not read."
        case .readyTimeout(let seconds): return "The server did not start within \(Int(seconds)) seconds."
        case .exitedBeforeReady(let exitStatus): return "The server stopped while starting (exit status \(exitStatus))."
        case .logUnavailable: return "\(displayName) could not write its log."
        }
    }

    private func showFailure(_ title: String, detail: String) {
        var tail: String?
        if let logURL {
            do {
                tail = try LogFiles.tail(of: logURL)
            } catch {
                tail = "The log could not be read: \(error)"
            }
        }
        status.showError(title: title, detail: detail, logTail: tail)
    }

    /// Stops the server off the main thread, then runs `then` on the main thread.
    ///
    /// The cover only goes up once the stop is really happening: showing it first and then
    /// refusing would leave the spinner over a screen whose buttons it had just hidden.
    ///
    /// - Parameter pendingWork: what `then` would have done, named for the log if a quit arrives
    ///   while the stop runs and cancels it. `nil` for the quit's own stop, which has none.
    private func stopServer(_ loadingMessage: String, pendingWork: String?, then: @escaping (StopOutcome) -> Void) {
        switch lifecycle.beginStop() {
        case .refused(let reason):
            presentAlert(reason, "Try again once it has finished.")
        case .nothingToStop:
            status.showLoading(loadingMessage)
            then(.notRunning)
        case .stop(let running):
            status.showLoading(loadingMessage)
            let log = logURL
            DispatchQueue.global().async {
                let outcome = running.stop()
                if let note = Self.logNote(for: outcome), let log {
                    Self.appendToLog(log, note)
                }
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    let completion = self.lifecycle.finishStop(running)
                    self.reportIfStopFailed(outcome)
                    switch completion {
                    case .resume:
                        then(outcome)
                    case .quitPending:
                        // Command-Q arrived while this stop ran. Starting the replacement now
                        // would hand the quit a server it has already decided not to stop.
                        if let pendingWork {
                            NSLog("PrepPal: %@ did not run because the app is quitting.", pendingWork)
                        }
                        NSApp.reply(toApplicationShouldTerminate: true)
                    }
                }
            }
        }
    }

    /// What a stop is worth writing into the server's own log. No number is quoted: the stop
    /// escalates through several waits, and the one the user sees is `worstCaseStopSeconds`.
    static func logNote(for outcome: StopOutcome) -> String? {
        switch outcome {
        case .notRunning, .stoppedGracefully:
            return nil
        case .killed:
            return "PrepPal: the server did not stop when it was asked; killed its process group\n"
        case .killFailed(let reason):
            return "PrepPal: the server could not be confirmed stopped, so a server process may still be running: \(reason)\n"
        }
    }

    /// A `killFailed` stop means a SIGKILL did not take and a server may still be holding the
    /// data folder. Only the user can deal with that, so it never stops at the log.
    private func reportIfStopFailed(_ outcome: StopOutcome) {
        guard case .killFailed(let reason) = outcome else { return }
        presentAlert("A \(Self.displayName) server may still be running.", """
            \(reason)

            Quit any leftover python3 process in Activity Monitor, or restart your Mac. Until \
            then the data folder stays locked and a new server cannot use it.
            """)
    }

    private static func appendToLog(_ log: URL, _ line: String) {
        do {
            let handle = try FileHandle(forWritingTo: log)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: Data(line.utf8))
        } catch {
            NSLog("PrepPal could not append to %@: %@", log.path, "\(error)")
        }
    }

    private func restartServer() {
        stopServer("Restarting the server…", pendingWork: "the restart") { [weak self] _ in
            self?.startServer()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        switch lifecycle.beginQuit() {
        case .terminateNow:
            return .terminateNow
        case .waitForWorkInFlight(let work):
            // A restart or an import is already stopping the server, or copying over its data
            // folder. Its completion starts nothing now that the quit has begun and answers the
            // quit instead, so waiting for it is what keeps this from becoming a second stop --
            // and the app from exiting over a server it has stopped holding.
            status.showLoading(Self.waitingMessage(for: work))
            return .terminateLater
        case .stopTheServer(let running):
            // Whichever branch of the stop's completion runs answers the quit exactly once.
            stopServer(Self.stoppingMessage(for: running), pendingWork: nil) { _ in
                sender.reply(toApplicationShouldTerminate: true)
            }
            return .terminateLater
        }
    }

    /// Truncating this understates the very bound the number exists to communicate.
    private static func stoppingMessage(for running: ServerController) -> String {
        "Stopping the server… (up to \(Int(ceil(running.worstCaseStopSeconds))) seconds if it does not answer)"
    }

    private static func waitingMessage(for work: ServerLifecycle.Work) -> String {
        switch work {
        case .stoppingServer(let running): return stoppingMessage(for: running)
        case .importingData: return "Finishing the import before quitting…"
        }
    }

    // MARK: Menus

    private func buildMenu() {
        let main = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About \(Self.displayName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",").target = self
        appMenu.addItem(withTitle: "Import Existing Data…", action: #selector(importExistingData), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "Copy MCP Command", action: #selector(copyMCPCommand), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "Open Logs", action: #selector(openLogsMenu), keyEquivalent: "").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(Self.displayName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        main.addItem(submenu: appMenu, title: Self.displayName)

        // Without an Edit menu, Command-C and Command-V do nothing inside a WKWebView.
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        main.addItem(submenu: edit, title: "Edit")

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload Page", action: #selector(reloadDesk), keyEquivalent: "r").target = self
        main.addItem(submenu: view, title: "View")

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        main.addItem(submenu: windowMenu, title: "Window")
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = main
    }

    @objc private func reloadDesk() {
        // Reloading is also the way out of a navigation failure that covered the desk, so the
        // cover comes off with it. `reload` refuses when there is no page to go back to, and
        // uncovering an empty web view would be the same dead end from the other side.
        guard web.reload() else { return }
        status.hide()
    }

    @objc private func openLogsMenu() {
        openLogs()
    }

    private func openLogs() {
        if let logURL {
            NSWorkspace.shared.open(logURL)
        } else if let locations {
            NSWorkspace.shared.open(locations.logsDirectory)
        }
    }

    @objc private func openSettings() {
        showSettings(reason: nil)
    }

    private func showSettings(reason: String?) {
        let alert = NSAlert()
        alert.messageText = "OpenRouter settings"
        alert.informativeText = (reason.map { $0 + "\n\n" } ?? "") + "Use a free model (its name ends in :free). Saving restarts the server."
        let key = NSSecureTextField(frame: NSRect(x: 0, y: 30, width: 360, height: 24))
        key.placeholderString = "OpenRouter API key"
        let model = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        model.placeholderString = "Model, for example qwen/qwen3-coder:free"
        do {
            key.stringValue = try secrets.read(SecretsStore.apiKeyAccount) ?? ""
            model.stringValue = try secrets.read(SecretsStore.modelAccount) ?? ""
        } catch {
            alert.informativeText += "\n\nCurrent values could not be read: \(error)"
        }
        let fields = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 54))
        fields.addSubview(key)
        fields.addSubview(model)
        alert.accessoryView = fields
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do {
            try saveOrClear(key.stringValue, SecretsStore.apiKeyAccount)
            try saveOrClear(model.stringValue, SecretsStore.modelAccount)
        } catch {
            presentAlert("The settings were not saved.", "\(error)")
            return
        }
        if locations != nil { restartServer() }
    }

    private func saveOrClear(_ value: String, _ account: String) throws {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            try secrets.delete(account)
        } else {
            try secrets.write(trimmed, for: account)
        }
    }

    @objc private func copyMCPCommand() {
        guard let locations = requireLocations() else { return }
        do {
            let json = try MCPCommand.configJSON(appBundle: Bundle.main.bundleURL, locations: locations)
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(json, forType: .string)
            presentAlert("MCP command copied.", "Paste it into your Claude Code MCP configuration.")
        } catch MCPCommandError.translocated {
            presentAlert("Move \(Self.displayName) to Applications first.",
                         "macOS is running the app from a temporary copy, so its path would change after a restart.")
        } catch {
            presentAlert("The MCP command could not be built.", "\(error)")
        }
    }

    @objc private func importExistingData() {
        guard let locations = requireLocations() else { return }
        let panel = NSOpenPanel()
        panel.message = "Choose an existing command_center folder to copy into \(Self.displayName)."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let source = panel.url else { return }

        let confirm = NSAlert()
        confirm.messageText = "Import \(source.lastPathComponent)?"
        confirm.informativeText = "The server stops while the folder is copied. The app's current data is moved to a backup folder, not deleted."
        confirm.addButton(withTitle: "Import")
        confirm.addButton(withTitle: "Cancel")
        guard confirm.runModal() == .alertFirstButtonReturn else { return }

        stopServer("Importing data…", pendingWork: "the import") { [weak self] _ in
            guard let self else { return }
            if let refusal = self.lifecycle.beginImportCopy() {
                presentAlert("Nothing was imported.", refusal)
                self.startServer()
                return
            }
            let importer = DataImporter()
            let destination = locations.commandCenterDirectory
            // The copy is unbounded -- it is the user's whole notebook -- and on the main thread
            // it freezes the window that is asking them to wait for it, spinner included.
            DispatchQueue.global().async {
                let report: (title: String, detail: String)
                do {
                    let result = try importer.importCommandCenter(from: source, to: destination)
                    report = ("Data imported.",
                              result.backup.map { "The previous app data is in \($0.path)." } ?? "There was no previous app data.")
                } catch {
                    report = ("Nothing was imported.",
                              Self.importFailureDetail(error, importer: importer, destination: destination))
                }
                DispatchQueue.main.async {
                    self.presentAlert(report.title, report.detail)
                    switch self.lifecycle.finishImportCopy() {
                    case .resume:
                        self.startServer()
                    case .quitPending:
                        NSApp.reply(toApplicationShouldTerminate: true)
                    }
                }
            }
        }
    }

    /// `DataImporter` puts the data back when a copy fails, but a rollback that could not finish
    /// says so only in the unified log and the error handed back here is the copy's. Reading what
    /// is actually on disk is what keeps a failed import recoverable rather than silent.
    static func importFailureDetail(_ failure: Error, importer: DataImporter, destination: URL) -> String {
        let readTheLog = "Open Console.app and search for PrepPal to see what the import reported."
        let state: ImportRecovery
        do {
            state = try importer.recovery(at: destination)
        } catch {
            return """
                \(failure)

                \(destination.path) could not be read afterwards either: \(error)
                \(readTheLog)
                """
        }
        guard state.dataFolderPresent else {
            // `backupsLeftBehind` is oldest first and includes folders earlier imports left
            // around. Only the newest one can be what this import moved aside, and sending the
            // student to a list of candidates is sending them to the wrong folder.
            guard let movedAside = state.backupsLeftBehind.last else {
                return """
                    \(failure)

                    There was no data to move aside and the half-copied folder was removed, so \
                    nothing of yours was lost. \(destination.path) does not exist; the server \
                    creates it empty the next time it starts.
                    \(readTheLog)
                    """
            }
            return """
                \(failure)

                \(destination.path) is missing, so putting your data back did not finish. Your \
                data is in:
                  \(movedAside.path)
                \(readTheLog)
                """
        }
        if state.backupsLeftBehind.isEmpty {
            return "\(failure)\n\nYour data is back the way it was."
        }
        let folders = state.backupsLeftBehind.map { "  \($0.path)" }.joined(separator: "\n")
        return """
            \(failure)

            Your data is back in place. An import also left these folders behind:
            \(folders)
            """
    }

    private func presentAlert(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.runModal()
    }
}

extension AppDelegate: NSMenuItemValidation {
    /// Settings and Import both stop the server, and a second stop inside the first one's window
    /// is refused. Closing them for the length of a stop is what keeps the user from choosing a
    /// folder, or retyping a key, only to be told afterwards that it could not be used.
    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        switch menuItem.action {
        case #selector(openSettings), #selector(importExistingData):
            return !lifecycle.isBusy
        case #selector(reloadDesk):
            return web.canReload
        default:
            return true
        }
    }
}

private extension NSMenu {
    func addItem(submenu: NSMenu, title: String) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.submenu = submenu
        addItem(item)
    }
}
