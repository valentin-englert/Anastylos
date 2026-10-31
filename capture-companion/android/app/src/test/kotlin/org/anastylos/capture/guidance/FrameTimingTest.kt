package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class FrameTimingTest {
    private class FakeBackend(
        var detections: List<PoseMath.MarkerDetection> = emptyList(),
        var solution: OpenCvPoseEstimator.ArucoBackend.PnpSolution? = null,
    ) : OpenCvPoseEstimator.ArucoBackend {
        var released = false
        override fun detect(
            gray: ByteArray,
            width: Int,
            height: Int,
            dictionary: Int,
        ): List<PoseMath.MarkerDetection> = detections

        override fun solve(
            objectMm: List<List<Double>>,
            imagePx: List<List<Double>>,
            intrinsics: PoseEstimator.Intrinsics,
        ): OpenCvPoseEstimator.ArucoBackend.PnpSolution? = solution

        override fun release() {
            released = true
        }
    }

    private fun frame(acc: TimingAccumulator): String? =
        acc.recordFrame(
            copyMs = 2.0, totalMs = 250.0, gapMs = 300.0, tsGapMs = 66.0,
            width = 1920, height = 1080,
            darkFraction = 0.07, saturatedFraction = 0.01, sharpness = 210.0,
        )

    @Test
    fun frameSummaryArrivesEverySixtiethFrame() {
        val acc = TimingAccumulator()
        repeat(59) { assertThat(frame(acc)).isNull() }
        val summary = frame(acc)
        assertThat(summary).isNotNull()
        assertThat(summary).contains("frames=60")
        assertThat(summary).contains("1920x1080")
        assertThat(summary).contains("totalAvg=250.0")
        assertThat(summary).contains("dark=0.070")
        assertThat(summary).contains("sharp=210.0")
    }

    @Test
    fun windowResetsAfterSummary() {
        val acc = TimingAccumulator(every = 2)
        assertThat(frame(acc)).isNull()
        assertThat(frame(acc)).contains("frames=2")
        assertThat(frame(acc)).isNull()
    }

    @Test
    fun backendAveragesUseOwnCallCounts() {
        val acc = TimingAccumulator(every = 1)
        acc.recordDetect(100.0)
        acc.recordDetect(200.0)
        acc.recordSolve(10.0)
        val summary = frame(acc)
        assertThat(summary).contains("detectAvg=150.0")
        assertThat(summary).contains("solveAvg=10.0")
    }

    @Test
    fun uiSummaryArrivesEverySixtiethEstimate() {
        val acc = TimingAccumulator()
        repeat(59) { assertThat(acc.recordUi(300L, 310.0)).isNull() }
        val summary = acc.recordUi(300L, 310.0)
        assertThat(summary).contains("ui=60")
        assertThat(summary).contains("ageAvg=300.0")
    }

    @Test
    fun decoratorDelegatesAndRecords() {
        val acc = TimingAccumulator(every = 1)
        val fake = FakeBackend()
        val timed = TimingArucoBackend(fake, acc)
        val gray = ByteArray(12)
        assertThat(timed.detect(gray, 4, 3, 0)).isEmpty()
        val intrinsics = PoseEstimator.Intrinsics(
            fx = 800.0, fy = 800.0, cx = 319.5, cy = 239.5,
            width = 640, height = 480, distortion = emptyList(),
            profileId = "test",
        )
        assertThat(timed.solve(listOf(listOf(0.0)), listOf(listOf(0.0)), intrinsics)).isNull()
        timed.release()
        assertThat(fake.released).isTrue()
        val summary = frame(acc)
        assertThat(summary).contains("detectAvg=")
        assertThat(summary).contains("solveAvg=")
    }
}
