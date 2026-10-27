// ANASTYLOS Capture — root build. Versions live in gradle/libs.versions.toml.

// Upgrades AGP 9's built-in Kotlin (bundled KGP 2.2.10) to the pinned KGP
// line; see the version catalog comment for why the 2.3 line is required.
buildscript {
    dependencies {
        classpath(libs.kotlin.gradle.plugin)
    }
}

plugins {
    alias(libs.plugins.agp) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.ksp) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
