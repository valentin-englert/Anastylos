package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SpeedTrackerTest {
    private val identity = listOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

    @Test
    fun needsTwoSamples() {
        val tracker = SpeedTracker()
        assertThat(tracker.observe(0L, Vec3(0.0, 0.0, -400.0), identity)).isNull()
    }

    @Test
    fun steadyMotionMeasured() {
        val tracker = SpeedTracker()
        tracker.observe(0L, Vec3(0.0, 0.0, -400.0), identity)
        // 8 mm in 100 ms = 80 mm/s.
        val speeds = tracker.observe(100L, Vec3(8.0, 0.0, -400.0), identity)!!
        assertThat(speeds.speedMmS).isWithin(1e-9).of(80.0)
        assertThat(speeds.angularSpeedDegS).isWithin(1e-9).of(0.0)
    }

    @Test
    fun stillCameraIsZero() {
        val tracker = SpeedTracker()
        tracker.observe(0L, Vec3(0.0, 0.0, -400.0), identity)
        val speeds = tracker.observe(200L, Vec3(0.0, 0.0, -400.0), identity)!!
        assertThat(speeds.speedMmS).isWithin(1e-9).of(0.0)
        assertThat(speeds.angularSpeedDegS).isWithin(1e-9).of(0.0)
    }

    @Test
    fun windowSlides() {
        val tracker = SpeedTracker(windowMs = 300L)
        tracker.observe(0L, Vec3(0.0, 0.0, -400.0), identity)
        // Jump far, then sit still: after the window slides past the
        // jump, speed returns to zero.
        tracker.observe(100L, Vec3(100.0, 0.0, -400.0), identity)
        val moving = tracker.observe(200L, Vec3(100.0, 0.0, -400.0), identity)!!
        assertThat(moving.speedMmS).isGreaterThan(0.0)
        val settled = tracker.observe(500L, Vec3(100.0, 0.0, -400.0), identity)!!
        assertThat(settled.speedMmS).isWithin(1e-9).of(0.0)
    }

    @Test
    fun resetClears() {
        val tracker = SpeedTracker()
        tracker.observe(0L, Vec3(0.0, 0.0, -400.0), identity)
        tracker.reset()
        assertThat(tracker.observe(100L, Vec3(8.0, 0.0, -400.0), identity)).isNull()
    }
}
