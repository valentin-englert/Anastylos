package org.anastylos.capture.ui.camera

import com.google.common.truth.Truth.assertThat
import org.anastylos.capture.camera.CameraCapabilities
import org.junit.Test

class WheelValuesTest {
    private val caps = CameraCapabilities(
        isoRange = 50..3200,
        exposureNsRange = 100_000L..1_000_000_000L,
        evStepsRange = -4..4,
        evStepSize = 0.5f,
        minFocusDistance = 10f,
    )

    @Test
    fun isoScaleIsLogarithmicAndStaysInRange() {
        val scale = isoScale(caps)!!
        assertThat(scale.fromFraction(0f)).isEqualTo(50)
        assertThat(scale.fromFraction(1f)).isEqualTo(3200)
        assertThat(scale.fromFraction(0.5f)).isEqualTo(400)
        assertThat(scale.toFraction(400)).isWithin(0.01f).of(0.5f)
        assertThat(isoScale(CameraCapabilities())).isNull()
    }

    @Test
    fun scalesRoundTripAnyValueThroughTheRuler() {
        val iso = isoScale(caps)!!
        assertThat(iso.fromFraction(iso.toFraction(777))).isEqualTo(777)
        val shutter = shutterScale(caps)!!
        assertThat(shutter.fromFraction(shutter.toFraction(20_000_000L))).isWithin(1000L).of(20_000_000L)
    }

    @Test
    fun shutterScaleNeverExceedsTheFrameDuration() {
        val scale = shutterScale(caps.copy(maxFrameDurationNs = 50_000_000L))!!
        assertThat(scale.fromFraction(1f)).isAtMost(50_000_000L)
    }

    @Test
    fun evScaleCommitsTheNearestDeviceStep() {
        val scale = evScale(caps)!!
        assertThat(scale.fromFraction(0.5f)).isEqualTo(0)
        assertThat(scale.fromFraction(0.56f)).isEqualTo(0)
        assertThat(scale.fromFraction(1f)).isEqualTo(4)
        assertThat(scale.format(-2)).isEqualTo("-1.0")
    }

    @Test
    fun focusScaleRunsFromInfinityToTheNearestDistance() {
        val scale = focusScale(caps)!!
        assertThat(scale.format(scale.fromFraction(0f))).isEqualTo("∞")
        assertThat(scale.fromFraction(1f)).isEqualTo(10f)
        assertThat(focusScale(CameraCapabilities())).isNull()
    }
}
