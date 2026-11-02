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
    fun isoStopsStayInsideTheSupportedRange() {
        assertThat(isoStops(caps).map { it.value }).containsExactly(50, 100, 200, 400, 800, 1600, 3200).inOrder()
        assertThat(isoStops(CameraCapabilities())).isEmpty()
    }

    @Test
    fun shutterStopsAreFasterThanTheLongestExposure() {
        val stops = shutterStops(caps.copy(maxFrameDurationNs = 50_000_000L))
        assertThat(stops.first().label).isEqualTo("1/8000")
        assertThat(stops.last().value).isAtMost(50_000_000L)
    }

    @Test
    fun evStopsAreSignedInStopsOfTheDeviceStep() {
        val stops = evStops(caps)
        assertThat(stops.first().label).isEqualTo("-2.0")
        assertThat(stops[4].label).isEqualTo("+0.0")
        assertThat(stops.last().label).isEqualTo("+2.0")
    }

    @Test
    fun focusStopsRunFromInfinityToTheNearestDistance() {
        val stops = focusStops(caps)
        assertThat(stops.first().label).isEqualTo("∞")
        assertThat(stops.last().value).isEqualTo(10f)
        assertThat(focusStops(CameraCapabilities())).isEmpty()
    }

    @Test
    fun nearestStopPicksTheClosestDetent() {
        val stops = isoStops(caps)
        assertThat(nearestStop(stops, 380)).isEqualTo(3)
        assertThat(nearestStop(emptyList<WheelStop<Int>>(), 100)).isEqualTo(0)
    }
}
