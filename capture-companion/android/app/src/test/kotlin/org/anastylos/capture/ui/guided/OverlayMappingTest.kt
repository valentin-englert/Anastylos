package org.anastylos.capture.ui.guided

import com.google.common.truth.Truth.assertThat
import org.anastylos.capture.camera.GuidedCamera
import org.junit.Test

class OverlayMappingTest {
    private fun geometry(
        analysisWidth: Int = 1280,
        analysisHeight: Int = 720,
        rotation: Int = 0,
        previewWidth: Int = 1280,
        previewHeight: Int = 720,
    ) = GuidedCamera.StreamGeometry(
        analysisWidth = analysisWidth,
        analysisHeight = analysisHeight,
        analysisRotationDeg = rotation,
        previewWidth = previewWidth,
        previewHeight = previewHeight,
    )

    @Test
    fun `identity fit maps corners exactly`() {
        val geo = geometry()
        val topLeft = OverlayMapping.mapToView(0f, 0f, geo, 1280f, 720f)
        assertThat(topLeft.x).isWithin(1e-3f).of(0f)
        assertThat(topLeft.y).isWithin(1e-3f).of(0f)
        val bottomRight = OverlayMapping.mapToView(1280f, 720f, geo, 1280f, 720f)
        assertThat(bottomRight.x).isWithin(1e-3f).of(1280f)
        assertThat(bottomRight.y).isWithin(1e-3f).of(720f)
    }

    @Test
    fun `portrait view letterboxes landscape frame`() {
        val geo = geometry()
        // Scale 720/1280 = 0.5625; frame 720x405 centered in 720x1280.
        val center = OverlayMapping.mapToView(640f, 360f, geo, 720f, 1280f)
        assertThat(center.x).isWithin(1e-3f).of(360f)
        assertThat(center.y).isWithin(1e-3f).of(640f)
        val topLeft = OverlayMapping.mapToView(0f, 0f, geo, 720f, 1280f)
        assertThat(topLeft.x).isWithin(1e-3f).of(0f)
        assertThat(topLeft.y).isWithin(1e-3f).of(437.5f)
    }

    @Test
    fun `rotation 90 maps raw top-left to upright top-right`() {
        val geo = geometry(rotation = 90, previewWidth = 720, previewHeight = 1280)
        assertThat(OverlayMapping.uprightFrame(geo)).isEqualTo(Pair(720, 1280))
        val mapped = OverlayMapping.mapToView(0f, 0f, geo, 720f, 1280f)
        assertThat(mapped.x).isWithin(1e-3f).of(720f)
        assertThat(mapped.y).isWithin(1e-3f).of(0f)
    }

    @Test
    fun `rotation 180 flips both axes`() {
        val geo = geometry(rotation = 180)
        val mapped = OverlayMapping.mapToView(0f, 0f, geo, 1280f, 720f)
        assertThat(mapped.x).isWithin(1e-3f).of(1280f)
        assertThat(mapped.y).isWithin(1e-3f).of(720f)
    }

    @Test
    fun `rotation 270 maps raw top-left to upright bottom-left`() {
        val geo = geometry(rotation = 270, previewWidth = 720, previewHeight = 1280)
        val mapped = OverlayMapping.mapToView(0f, 0f, geo, 720f, 1280f)
        assertThat(mapped.x).isWithin(1e-3f).of(0f)
        assertThat(mapped.y).isWithin(1e-3f).of(1280f)
    }

    @Test
    fun `rotation normalizes beyond 360`() {
        val geo = geometry(rotation = 450, previewWidth = 720, previewHeight = 1280)
        val mapped = OverlayMapping.mapToView(0f, 0f, geo, 720f, 1280f)
        assertThat(mapped.x).isWithin(1e-3f).of(720f)
        assertThat(mapped.y).isWithin(1e-3f).of(0f)
    }

    @Test
    fun `aspects match for equal streams and rotated equivalents`() {
        assertThat(OverlayMapping.aspectsMatch(geometry())).isTrue()
        assertThat(
            OverlayMapping.aspectsMatch(
                geometry(rotation = 90, previewWidth = 720, previewHeight = 1280),
            ),
        ).isTrue()
    }

    @Test
    fun `aspects mismatch hides the overlay`() {
        assertThat(
            OverlayMapping.aspectsMatch(geometry(previewWidth = 640, previewHeight = 480)),
        ).isFalse()
        assertThat(OverlayMapping.aspectsMatch(geometry(analysisWidth = 0))).isFalse()
        assertThat(OverlayMapping.aspectsMatch(geometry(previewHeight = 0))).isFalse()
    }
}
