// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Typhoon",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Typhoon", targets: ["TyphoonApp"]),
    ],
    targets: [
        .executableTarget(name: "TyphoonApp", path: "Sources/TyphoonApp"),
    ],
    swiftLanguageVersions: [.v5]
)
