package org.anastylos.capture.camera

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Bundled CAL-01 profile reuse is bound to the measured phone: the
 * profile schema carries no device field and every phone has a
 * camera "0" (GUIDE-01 finding on a second phone).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class AssetMeasuredProfileSourceTest {
    private fun source(model: String) =
        AssetMeasuredProfileSource(RuntimeEnvironment.getApplication(), model)

    @Test
    fun calibratedPhoneGetsMeasuredAndScaledProfile() {
        val src = source(CAL01_DEVICE_MODEL)
        val full = src.forGeometry("0", 1920, 1080, 0f, FocusMode.MANUAL, 2.5f)
        val scaled = src.forGeometry("0", 1280, 720, 0f, FocusMode.MANUAL, 2.5f)
        assertThat(full?.profileId).startsWith("measured:")
        assertThat(scaled?.profileId).endsWith("@1280x720-scaled")
        assertThat(src.calibratedOptics("0")?.focusDiopters).isEqualTo(2.5f)
    }

    @Test
    fun otherPhoneNeverGetsTheBundledProfile() {
        val src = source("Pixel 9")
        assertThat(src.forGeometry("0", 1920, 1080, 0f, FocusMode.MANUAL, 2.5f)).isNull()
        assertThat(src.forGeometry("0", 1280, 720, 0f, FocusMode.MANUAL, 2.5f)).isNull()
        assertThat(src.calibratedOptics("0")).isNull()
        assertThat(src.mismatch("0", 1280, 720, 0f, FocusMode.AUTO, 0f)).isNull()
    }

    @Test
    fun mismatchExplainsFocusOnCalibratedPhone() {
        val reason = source(CAL01_DEVICE_MODEL)
            .mismatch("0", 1280, 720, 0f, FocusMode.AUTO, 0f)
        assertThat(reason).contains("focus")
        assertThat(reason).contains("2.50 D")
    }

    @Test
    fun referenceCitesTheExactBundledBytes() {
        val bytes = java.io.File("src/main/assets/measured-profile-M2101K6G.json").readBytes()
        val sha = java.security.MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { "%02x".format(it) }
        val ref = source("M2101K6G").reference("e92c8ea1-36a8-449f-8df9-50f0f3481548")!!
        assertThat(ref.sha256).isEqualTo(sha)
        assertThat(ref.sensorMode).isEqualTo("0-1920x1080-still-MF40cm-AE-EV0")
        assertThat(ref.pixelFrameId).isEqualTo("cam0-native-1920x1080")
        // Another phone never cites this profile.
        assertThat(source("Pixel 9").reference("e92c8ea1-36a8-449f-8df9-50f0f3481548")).isNull()
    }

    @Test
    fun secondPhoneGetsOnlyItsOwnProfile() {
        val xiaomi = source("M2101K6G").forGeometry("0", 1280, 720, 0f, FocusMode.MANUAL, 2.5f)
        val samsung = source(CAL01_DEVICE_MODEL).forGeometry("0", 1280, 720, 0f, FocusMode.MANUAL, 2.5f)
        assertThat(xiaomi?.profileId).startsWith("measured:")
        assertThat(xiaomi?.profileId).endsWith("@1280x720-scaled")
        assertThat(xiaomi?.profileId).isNotEqualTo(samsung?.profileId)
        val doc = org.anastylos.capture.guidance.CalibrationProfile.parse(
            java.io.File("src/main/assets/measured-profile-M2101K6G.json").readText(),
        )!!
        assertThat(xiaomi!!.fx).isWithin(1e-9).of(doc.k[0] * 1280 / 1920)
    }
}
