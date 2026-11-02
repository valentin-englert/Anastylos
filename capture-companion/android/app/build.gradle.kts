plugins {
    alias(libs.plugins.agp)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "org.anastylos.capture"
    // 37: current androidx releases declare minCompileSdk 37. Target stays 36
    // (latest behavior-verified platform); see targetSdk rationale below.
    compileSdk = 37

    defaultConfig {
        applicationId = "org.anastylos.capture"
        // Deliberate floors: 26 covers Keystore attestation-era APIs, TLS 1.3,
        // notification channels and scoped storage without legacy workarounds,
        // while keeping 2017-era devices usable as capture phones.
        minSdk = 26
        // Android 16: the latest platform whose behavior changes are verified
        // for this app. 37 (Android 17) is compiled against for library
        // compatibility; targeting it follows physical-device validation
        // (see LIMITATIONS). Never an obsolete target to dodge permissions.
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    lint {
        lintConfig = rootProject.file("lint.xml")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
        }
    }
}

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}

// Robolectric's FileDescriptor shadow reflects into jdk.internal.access.
// AGP 9 runs Gradle on JDK 21+, where that package is closed by default.
// Robolectric's SDK 36 sandbox needs Java 21: pin the test JVM so a
// Gradle started on JDK 17 fails loudly instead of 4 sandbox errors.
tasks.withType<org.gradle.api.tasks.testing.Test>().configureEach {
    jvmArgs("--add-opens", "java.base/jdk.internal.access=ALL-UNNAMED")
    javaLauncher.set(javaToolchains.launcherFor { languageVersion.set(JavaLanguageVersion.of(21)) })
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel)
    implementation(libs.androidx.lifecycle.runtime)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.splashscreen)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.work.runtime)
    implementation(libs.camerax.camera2)
    implementation(libs.camerax.core)
    implementation(libs.camerax.lifecycle)
    implementation(libs.camerax.view)
    implementation(libs.mlkit.barcode.scanning)
    implementation(libs.androidx.security.crypto)
    ksp(libs.androidx.room.compiler)
    implementation(platform(libs.kotlinx.coroutines.bom))
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.kotlinx.serialization.json)
    // ArUco detection + PnP pose (Wave-2 guided capture). All 4 ABIs ship
    // in the APK (x86_64 required for emulator verification).
    implementation(libs.opencv.android)

    testImplementation(libs.junit)
    testImplementation(libs.truth)
    testImplementation(libs.androidx.core.testing)
    testImplementation(libs.androidx.room.testing)
    testImplementation(libs.androidx.work.testing)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.robolectric)
    // BOM platforms do not propagate across AGP test configurations on
    // their own; without this, versionless BOM-managed test artifacts
    // (ui-test-junit4) fail to resolve when the androidTest classpath
    // is built (lint model, connected tests).
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.test.runner)
}
