import Foundation
import SwiftUI

struct ACDevice: Decodable, Identifiable, Sendable {
    let host: String
    let mac: String
    let devtype: String?
    let name: String?
    let locked: Bool?
    let port: Int?
    let cloudId: String?
    let transport: String?
    var id: String { cloudId ?? mac }
    var isCloud: Bool { transport == "cloud" }
    var controlIdentity: String? {
        if isCloud { return cloudId.map { "cloud:" + $0 } }
        return "local:" + mac.lowercased().replacingOccurrences(of: "-", with: ":")
    }
    var title: String { name.flatMap { $0.isEmpty ? nil : $0 } ?? "Tornado air conditioner" }

    init(host: String, mac: String, devtype: String? = nil, name: String? = nil, port: Int? = nil) {
        self.host = host; self.mac = mac; self.devtype = devtype; self.name = name; self.port = port; locked = nil
        cloudId = nil; transport = nil
    }
    enum CodingKeys: String, CodingKey {
        case host, mac, devtype, name, locked, port, transport
        case cloudId = "id"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        host = try values.decode(String.self, forKey: .host)
        mac = try values.decode(String.self, forKey: .mac)
        name = try values.decodeIfPresent(String.self, forKey: .name)
        locked = try values.decodeIfPresent(Bool.self, forKey: .locked)
        port = try values.decodeIfPresent(Int.self, forKey: .port)
        cloudId = try values.decodeIfPresent(String.self, forKey: .cloudId)
        transport = try values.decodeIfPresent(String.self, forKey: .transport)
        if let value = try? values.decode(String.self, forKey: .devtype) { devtype = value }
        else if let value = try? values.decode(Int.self, forKey: .devtype) { devtype = String(format: "0x%04x", value) }
        else { devtype = nil }
    }
}

struct ACState: Decodable, Sendable {
    let power: Bool
    let temperature: Double
    let ambientTemperature: Double?
    let mode: String
    let fan: String
    let display: Bool?
    let swing: Bool?
}

struct BackendResponse: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let device: ACDevice?
    let devices: [ACDevice]?
    let state: ACState?
    let verified: Bool?
    let homekit: HomeKitBridgeState?
}

enum ControllerError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let message): return message }
    }
}

enum Backend {
    static func run(_ arguments: [String], input: Data? = nil) async throws -> BackendResponse {
        try await Task.detached(priority: .userInitiated) { try execute(arguments, input: input) }.value
    }

    private static func execute(_ arguments: [String], input: Data?) throws -> BackendResponse {
        let process = Process()
        let files = FileManager.default
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("typhoon-backend")
        if let bundled, files.isExecutableFile(atPath: bundled.path) {
            process.executableURL = bundled
            process.arguments = ["--json"] + arguments
        } else {
            let root = ProcessInfo.processInfo.environment["TYPHOON_PROJECT_DIR"] ?? files.currentDirectoryPath
            let python = URL(fileURLWithPath: root).appendingPathComponent(".venv/bin/python")
            guard files.isExecutableFile(atPath: python.path), files.fileExists(atPath: root + "/typhoon") else {
                throw ControllerError.message("The controller is missing from this app. Rebuild Typhoon with scripts/build-app.sh, then open the complete Typhoon.app.")
            }
            process.currentDirectoryURL = URL(fileURLWithPath: root)
            process.executableURL = python
            process.arguments = ["-m", "typhoon", "--json"] + arguments
        }
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        let inputPipe = input.map { _ in Pipe() }
        if let inputPipe { process.standardInput = inputPipe }
        else { process.standardInput = FileHandle.nullDevice }
        guard (input?.count ?? 0) <= 16_384 else {
            throw ControllerError.message("The sign-in details are too long. Check the email and password fields.")
        }
        try process.run()
        let timeout = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 60, execute: timeout)
        defer { timeout.cancel() }
        if let input, let inputPipe {
            do {
                try inputPipe.fileHandleForWriting.write(contentsOf: input)
                try inputPipe.fileHandleForWriting.close()
            } catch {
                try? inputPipe.fileHandleForWriting.close()
                if process.isRunning { process.terminate() }
                process.waitUntilExit()
                throw ControllerError.message("The controller could not receive the sign-in details. Try signing in again.")
            }
        }
        let bytes = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        timeout.cancel()
        guard let response = try? JSONDecoder().decode(BackendResponse.self, from: bytes) else {
            if process.terminationReason == .uncaughtSignal {
                throw ControllerError.message("The connection did not answer in time. Check the air conditioner is online, then refresh.")
            }
            throw ControllerError.message("The controller returned an unreadable response (exit \(process.terminationStatus)). Check the app installation and try again.")
        }
        guard response.ok, process.terminationStatus == 0 else {
            throw ControllerError.message(response.error ?? "The air conditioner could not complete this request.")
        }
        return response
    }
}

@MainActor
final class ClimateController: ObservableObject {
    @Published var device: ACDevice?
    @Published var state: ACState?
    @Published var discovered: [ACDevice] = []
    @Published var cloudDevices: [ACDevice] = []
    @Published var hasListedCloud = false
    @Published var cloudSessionAvailable = false
    @Published var busy = false
    @Published var connected = false
    @Published var setupPresented = false
    @Published var error: String?
    @Published var activity = "Starting controller…"
    @Published var lastRead: Date?
    @Published var confirmation: String?
    @Published var hasSearched = false
    private var started = false
    private var refreshTask: Task<Void, Never>?
    deinit { refreshTask?.cancel() }

    var canControl: Bool { connected && !busy && state != nil && device?.controlIdentity != nil }
    var usesCloud: Bool { device?.isCloud == true }

    func start() async {
        guard !started else { return }; started = true
        // Keep menu-bar state current even with every window closed. Retry
        // transient network failures after Wi-Fi reconnects or the Mac wakes.
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                guard let self else { return }
                guard !self.setupPresented else { continue }
                await self.refresh()
            }
        }
        busy = true
        defer { busy = false }
        do {
            let config = try await Backend.run(["config"])
            device = config.device
            cloudSessionAvailable = device?.isCloud == true
            if device == nil { activity = "Ready to connect"; setupPresented = true; return }
            activity = "Connecting to air conditioner…"
            try await readState()
        } catch { fail(error) }
    }

    func refresh() async {
        guard !busy, device != nil else { return }
        busy = true; error = nil; activity = "Reading air conditioner…"
        defer { busy = false }
        do { try await readState() } catch { fail(error) }
    }

    func discover(host: String = "") async {
        guard !busy else { return }
        busy = true; error = nil; discovered = []; hasSearched = false
        activity = "Looking on your local network…"
        defer { busy = false; hasSearched = true }
        do {
            var args = ["discover", "--timeout", "5"]
            if !host.trimmingCharacters(in: .whitespaces).isEmpty { args += ["--host", host.trimmingCharacters(in: .whitespaces)] }
            discovered = try await Backend.run(args).devices ?? []
            activity = discovered.isEmpty ? "No air conditioners found" : "Choose your air conditioner"
        } catch { self.error = error.localizedDescription; activity = "Discovery could not finish" }
    }

    func connect(_ selected: ACDevice) async {
        guard !busy else { return }
        busy = true; error = nil; confirmation = nil; connected = false
        activity = "Connecting to \(selected.title)…"
        defer { busy = false }
        do {
            var args: [String]
            if selected.isCloud {
                guard let id = selected.cloudId else { throw ControllerError.message("This cloud device has no identity. Refresh the account's device list.") }
                args = ["cloud-select", "--id", id]
            } else {
                args = ["configure", "--host", selected.host, "--mac", selected.mac, "--name", selected.title]
                if let type = selected.devtype { args += ["--devtype", type] }
                if let port = selected.port { args += ["--port", String(port)] }
            }
            _ = try await Backend.run(args)
            device = selected; state = nil; lastRead = nil
            try await readState()
            setupPresented = false
        } catch { fail(error) }
    }

    func cloudLogin(email: String, password: String, region: String) async {
        guard !busy else { return }
        busy = true; error = nil; cloudDevices = []; hasListedCloud = false
        // A login may replace the account and change the CLI's active device.
        // Require selection and a fresh read before enabling any controls.
        connected = false; device = nil; state = nil; lastRead = nil; confirmation = nil
        activity = "Signing in to Tornado…"
        defer { busy = false }
        do {
            let input = try JSONSerialization.data(withJSONObject: ["email": email.trimmingCharacters(in: .whitespacesAndNewlines), "password": password, "region": region])
            cloudDevices = try await Backend.run(["cloud-login", "--stdin"], input: input).devices ?? []
            hasListedCloud = true; cloudSessionAvailable = true
            activity = cloudDevices.isEmpty ? "No air conditioners in this account" : "Choose your air conditioner"
        } catch { self.error = error.localizedDescription; activity = "Sign-in could not finish" }
    }

    func listCloudDevices() async {
        guard !busy else { return }
        busy = true; error = nil; cloudDevices = []; hasListedCloud = false
        activity = "Reading your Tornado devices…"
        defer { busy = false }
        do {
            cloudDevices = try await Backend.run(["cloud-devices"]).devices ?? []
            hasListedCloud = true; cloudSessionAvailable = true
            activity = cloudDevices.isEmpty ? "No air conditioners in this account" : "Choose your air conditioner"
        } catch { self.error = error.localizedDescription; activity = "Could not read the saved session" }
    }

    func cloudLogout() async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            _ = try await Backend.run(["cloud-logout"])
            cloudDevices = []; hasListedCloud = false; cloudSessionAvailable = false
            if usesCloud { device = nil; state = nil; connected = false; lastRead = nil; confirmation = nil }
            activity = connected ? "Connected over Wi‑Fi" : "Tornado account signed out"
        } catch { self.error = error.localizedDescription }
    }

    func set(_ key: String, _ value: String) async {
        guard canControl, let expectedDevice = device?.controlIdentity else { return }
        busy = true; error = nil; confirmation = nil
        activity = "Applying change…"
        defer { busy = false }
        do {
            _ = try await Backend.run(["set", "--expected-device", expectedDevice, "--" + key, value])
            activity = "Checking air conditioner…"
            try await readState()
            guard device?.controlIdentity == expectedDevice else {
                throw ControllerError.message("The selected air conditioner changed during the request. Refresh and check the selected device before making another change.")
            }
            guard let state, matches(state, key: key, value: value) else {
                throw ControllerError.message("The latest readback reported a different setting. The reported state is shown; refresh before trying again.")
            }
            confirmation = usesCloud ? "Settings reported by Tornado cloud" : "Settings reported by air conditioner"
        } catch { fail(error) }
    }

    private func readState() async throws {
        let response = try await Backend.run(["status"])
        guard let current = response.state else { throw ControllerError.message("The air conditioner did not report its settings.") }
        state = current
        if let selected = response.device { device = selected }
        guard response.verified == true else {
            throw ControllerError.message("Settings were returned without a verified fresh readback. Refresh to establish a live connection.")
        }
        lastRead = Date(); connected = true
        activity = usesCloud ? "Connected through Tornado cloud" : "Connected over Wi‑Fi"
        error = nil
    }

    private func matches(_ state: ACState, key: String, value: String) -> Bool {
        switch key {
        case "power": return state.power == (value == "on")
        case "temperature": return abs(state.temperature - (Double(value) ?? -100)) < 0.1
        case "mode": return state.mode == value
        case "fan": return state.fan == value
        case "display": return state.display == (value == "on")
        case "swing": return state.swing == (value == "on")
        default: return false
        }
    }

    private func fail(_ error: Error) {
        self.error = error.localizedDescription; connected = false
        confirmation = nil; activity = "Connection needs attention"
    }
}
