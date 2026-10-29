package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/** H6 interim image-quality verdicts (preregistration + §8). */
class ImageQualityTest {
    private fun good() = ImageQualityInput(
        sharpness = 100.0,
        sharpReference = 100.0,
        darkFraction = 0.01,
        saturatedFraction = 0.01,
        motionMedianPx = 0.2,
        motionP90Px = 0.5,
    )

    @Test
    fun allGoodAccepts() {
        assertThat(evaluateImageQuality(good())).isEqualTo(ImageQualityVerdict.Accepted)
    }

    @Test
    fun softImageRejects() {
        assertThat(evaluateImageQuality(good().copy(sharpness = 69.0)))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("sharpness")))
    }

    @Test
    fun sharpnessBoundaryPasses() {
        assertThat(evaluateImageQuality(good().copy(sharpness = 70.0)))
            .isEqualTo(ImageQualityVerdict.Accepted)
    }

    @Test
    fun darkRejects() {
        assertThat(evaluateImageQuality(good().copy(darkFraction = 0.05)))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("dark")))
        assertThat(evaluateImageQuality(good().copy(darkFraction = 0.049)))
            .isEqualTo(ImageQualityVerdict.Accepted)
    }

    @Test
    fun saturatedRejects() {
        assertThat(evaluateImageQuality(good().copy(saturatedFraction = 0.05)))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("saturated")))
    }

    @Test
    fun motionRejectsOnce() {
        assertThat(evaluateImageQuality(good().copy(motionMedianPx = 0.7)))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("motion")))
        assertThat(evaluateImageQuality(good().copy(motionP90Px = 1.5)))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("motion")))
        assertThat(evaluateImageQuality(good().copy(motionMedianPx = 2.0, motionP90Px = 3.0)))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("motion")))
    }

    @Test
    fun unknownStaysUnknown() {
        assertThat(evaluateImageQuality(good().copy(sharpness = null)))
            .isEqualTo(ImageQualityVerdict.Unknown)
        assertThat(
            evaluateImageQuality(
                ImageQualityInput(null, null, null, null, null, null),
            ),
        ).isEqualTo(ImageQualityVerdict.Unknown)
    }

    @Test
    fun rejectBeatsUnknown() {
        val input = good().copy(sharpness = null, darkFraction = 0.5)
        assertThat(evaluateImageQuality(input))
            .isEqualTo(ImageQualityVerdict.Rejected(listOf("dark")))
    }

    @Test
    fun zeroReferenceIsUnknownNotCrash() {
        assertThat(evaluateImageQuality(good().copy(sharpReference = 0.0)))
            .isEqualTo(ImageQualityVerdict.Unknown)
    }
}
