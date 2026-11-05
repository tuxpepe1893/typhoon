import AppKit

@MainActor
final class AppLifecycle: NSObject, NSApplicationDelegate {
    let controller = ClimateController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        Task { await controller.start() }
        if CommandLine.arguments.contains("--background") {
            // A login launch must not interrupt the app the person is using.
            DispatchQueue.main.async { NSApp.hide(nil) }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
