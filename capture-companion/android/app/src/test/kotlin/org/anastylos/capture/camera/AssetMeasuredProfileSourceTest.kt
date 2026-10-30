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
        val src = source("M2101K6G")
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
}
