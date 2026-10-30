package org.anastylos.capture.camera

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SettingsApplierTest {
    /** cam0 of the SM-A536B (dumpsys media.camera, GUIDE-01). */
    private val a536Cam0 = listOf(15 to 15, 15 to 20, 20 to 20, 24 to 24, 8 to 30, 10 to 30, 15 to 30, 30 to 30)

    @Test
    fun trackingRangePinsThirtyFpsWhenOffered() {
        assertThat(trackingFpsRange(a536Cam0)).isEqualTo(30 to 30)
    }

    @Test
    fun trackingRangeNeverExceedsTrackingFps() {
        assertThat(trackingFpsRange(listOf(15 to 30, 60 to 60, 30 to 60))).isEqualTo(15 to 30)
    }

    @Test
    fun trackingRangePrefersHighestFloor() {
        assertThat(trackingFpsRange(listOf(8 to 30, 24 to 24, 15 to 30))).isEqualTo(24 to 24)
    }

    @Test
    fun trackingRangeAbsentWithoutUsableRanges() {
        assertThat(trackingFpsRange(emptyList())).isNull()
        assertThat(trackingFpsRange(listOf(60 to 120, 0 to 30))).isNull()
    }
}
