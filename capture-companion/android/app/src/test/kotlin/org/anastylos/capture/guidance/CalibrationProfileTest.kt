package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.anastylos.capture.camera.FocusMode
import org.junit.Test
import java.io.File

/**
 * Measured-profile parsing and compatibility gates (CAL-01). The
 * shipped asset is loaded from the main source set so these tests
 * pin the exact bytes the app will read on device; inline mutations
 * cover every rejection path without weakening the real profile.
 */
class CalibrationProfileTest {
    private fun assetText(): String {
        val direct = File("src/main/assets/cal01-profile.json")
        assertThat(direct.isFile).isTrue()
        return direct.readText()
    }

    private fun validDoc(json: String = assetText()) = CalibrationProfile.parse(json)!!

    @Test
    fun assetParsesWithKnownValues() {
        val doc = CalibrationProfile.parse(assetText())
        assertThat(doc).isNotNull()
        doc!!
        assertThat(doc.schema).isEqualTo("anastylos-camera-calibration@1")
        assertThat(doc.cameraId).isEqualTo("0")
        assertThat(doc.referenceSizePx).containsExactly(1920, 1080).inOrder()
        assertThat(doc.k[0]).isWithin(0.01).of(1395.78)
        assertThat(doc.k[4]).isWithin(0.01).of(1396.32)
        assertThat(doc.k[2]).isWithin(0.01).of(956.27)
        assertThat(doc.k[5]).isWithin(0.01).of(556.84)
        assertThat(doc.distortion).hasSize(5)
        assertThat(doc.metricValidity).isEqualTo("validated")
        assertThat(doc.focusDiopters).isNotNull()
        assertThat(doc.focusDiopters!!).isWithin(1e-9).of(2.5)
    }

    @Test
    fun exactGeometryAndOpticsAreCompatible() {
        val doc = validDoc()
        assertThat(
            CalibrationProfile.isCompatible(
                doc, "0", 1920, 1080, 0f, FocusMode.MANUAL, 2.5f,
            ),
        ).isTrue()
    }

    @Test
    fun eachMismatchIsIncompatible() {
        val doc = validDoc()
        fun check(
            cameraId: String? = "0",
            width: Int = 1920,
            height: Int = 1080,
            zoom: Float = 0f,
            focusMode: FocusMode = FocusMode.MANUAL,
            diopters: Float = 2.5f,
        ) = CalibrationProfile.isCompatible(
            doc, cameraId, width, height, zoom, focusMode, diopters,
        )
        assertThat(check(cameraId = "1")).isFalse()
        assertThat(check(cameraId = null)).isFalse()
        assertThat(check(width = 1920, height = 1200)).isFalse()
        assertThat(check(width = 1280, height = 960)).isFalse()
        assertThat(check(width = 3840, height = 2160)).isFalse()
        assertThat(check(width = 1281, height = 720)).isFalse()
        assertThat(check(zoom = 0.04f)).isFalse()
        assertThat(check(focusMode = FocusMode.AUTO)).isFalse()
        assertThat(check(diopters = 2.0f)).isFalse()
        assertThat(check(diopters = 2.5f + 0.06f)).isFalse()
    }

    @Test
    fun focusToleranceAcceptsTapQuantization() {
        // The CAL-01 setup tap landed at 2.4668 D for nominal 2.5 D;
        // that measured quantization must stay compatible.
        val doc = validDoc()
        assertThat(
            CalibrationProfile.isCompatible(
                doc, "0", 1920, 1080, 0f, FocusMode.MANUAL, 2.4668028f,
            ),
        ).isTrue()
        assertThat(
            CalibrationProfile.isCompatible(
                doc, "0", 1920, 1080, 0f, FocusMode.MANUAL, 2.5f + 0.04f,
            ),
        ).isTrue()
    }

    @Test
    fun unvalidatedProfileParsesButNeverCompatible() {
        val mutated = assetText().replace(
            "\"metric_validity\": \"validated\"",
            "\"metric_validity\": \"unknown\"",
        )
        val doc = CalibrationProfile.parse(mutated)
        assertThat(doc).isNotNull()
        assertThat(
            CalibrationProfile.isCompatible(
                doc!!, "0", 1920, 1080, 0f, FocusMode.MANUAL, 2.5f,
            ),
        ).isFalse()
    }

    @Test
    fun structuralCorruptionFailsParse() {
        assertThat(CalibrationProfile.parse("not json")).isNull()
        assertThat(CalibrationProfile.parse("{}")).isNull()
        assertThat(CalibrationProfile.parse("")).isNull()
        val base = assetText()
        assertThat(
            CalibrationProfile.parse(
                base.replace("anastylos-camera-calibration@1", "other-schema@9"),
            ),
        ).isNull()
        assertThat(
            CalibrationProfile.parse(base.replace("brown_conrady_5", "fisheye")),
        ).isNull()
        assertThat(
            CalibrationProfile.parse(
                base.replaceFirst(Regex("\"K\": \\[[^\\]]*\\]"), "\"K\": []"),
            ),
        ).isNull()
        assertThat(
            CalibrationProfile.parse(
                base.replaceFirst(Regex("\"distortion\": \\[[^\\]]*\\]"), "\"distortion\": [0.0]"),
            ),
        ).isNull()
        assertThat(
            CalibrationProfile.parse(
                base.replaceFirst(Regex("\"profile_id\": \"[^\"]*\""), "\"profile_id\": \"\""),
            ),
        ).isNull()
    }

    @Test
    fun toIntrinsicsMapsSolverPixels() {
        val doc = validDoc()
        val i = CalibrationProfile.toIntrinsics(doc)
        assertThat(i.fx).isWithin(1e-9).of(doc.k[0])
        assertThat(i.fy).isWithin(1e-9).of(doc.k[4])
        assertThat(i.cx).isWithin(1e-9).of(doc.k[2])
        assertThat(i.cy).isWithin(1e-9).of(doc.k[5])
        assertThat(i.width).isEqualTo(1920)
        assertThat(i.height).isEqualTo(1080)
        assertThat(i.distortion).containsExactlyElementsIn(doc.distortion).inOrder()
        assertThat(i.profileId).startsWith("measured:")
        assertThat(i.profileId).contains(doc.profileId)
        assertThat(i.profileId).doesNotContain("uncalibrated")
    }

    @Test
    fun sameAspectDownscaleIsCompatible() {
        val doc = validDoc()
        assertThat(
            CalibrationProfile.isCompatible(doc, "0", 1280, 720, 0f, FocusMode.MANUAL, 2.5f),
        ).isTrue()
        assertThat(
            CalibrationProfile.isCompatible(doc, "0", 960, 540, 0f, FocusMode.MANUAL, 2.5f),
        ).isTrue()
        // Optics still bind a scaled grid.
        assertThat(
            CalibrationProfile.isCompatible(doc, "0", 1280, 720, 0f, FocusMode.AUTO, 2.5f),
        ).isFalse()
        assertThat(
            CalibrationProfile.isCompatible(doc, "1", 1280, 720, 0f, FocusMode.MANUAL, 2.5f),
        ).isFalse()
    }

    @Test
    fun scaledIntrinsicsUsePixelCentreConvention() {
        val doc = validDoc()
        val i = CalibrationProfile.toIntrinsics(doc, 1280, 720)
        val s = 1280.0 / 1920.0
        assertThat(i.fx).isWithin(1e-9).of(doc.k[0] * s)
        assertThat(i.fy).isWithin(1e-9).of(doc.k[4] * s)
        assertThat(i.cx).isWithin(1e-9).of((doc.k[2] + 0.5) * s - 0.5)
        assertThat(i.cy).isWithin(1e-9).of((doc.k[5] + 0.5) * s - 0.5)
        assertThat(i.width).isEqualTo(1280)
        assertThat(i.height).isEqualTo(720)
        assertThat(i.distortion).containsExactlyElementsIn(doc.distortion).inOrder()
        assertThat(i.profileId).isEqualTo("measured:${doc.profileId}@1280x720-scaled")
        assertThat(i.profileId).doesNotContain("uncalibrated")
    }

    @Test
    fun scaledProjectionMatchesRecordedGrid() {
        // A ray projected with the scaled K lands on the same physical
        // point of the image: grid corner (-0.5) mapping is preserved.
        val doc = validDoc()
        val full = CalibrationProfile.toIntrinsics(doc)
        val small = CalibrationProfile.toIntrinsics(doc, 1280, 720)
        val s = 1280.0 / 1920.0
        val x = 0.21
        val y = -0.13
        val uFull = full.fx * x + full.cx
        val vFull = full.fy * y + full.cy
        assertThat(small.fx * x + small.cx).isWithin(1e-9).of((uFull + 0.5) * s - 0.5)
        assertThat(small.fy * y + small.cy).isWithin(1e-9).of((vFull + 0.5) * s - 0.5)
    }

    @Test
    fun recordedGridKeepsPlainProfileId() {
        val doc = validDoc()
        assertThat(CalibrationProfile.toIntrinsics(doc, 1920, 1080).profileId)
            .isEqualTo("measured:${doc.profileId}")
    }
}
