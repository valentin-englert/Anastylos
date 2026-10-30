package org.anastylos.capture.camera

import android.hardware.camera2.CameraMetadata
import androidx.camera.core.ImageCapture
import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Settings sanitize/serialize/format/applied-format (pure):
 * clamping into the supported envelope, strict versioned
 * round-trip, and requested-vs-applied display strings.
 */
class CameraSettingsTest {
    private fun fullCaps() = CameraCapabilities(
        aeModes = setOf(CameraMetadata.CONTROL_AE_MODE_OFF, CameraMetadata.CONTROL_AE_MODE_ON),
        isoRange = 50..3200,
        exposureNsRange = 1_000L..100_000_000L,
        maxFrameDurationNs = 200_000_000L,
        evStepsRange = -20..20,
        evStepSize = 0.1f,
        afModes = setOf(CameraMetadata.CONTROL_AF_MODE_OFF, CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE),
        minFocusDistance = 10f,
        awbModes = setOf(1, 2, 3, 5, 6),
        maxDigitalZoom = 8f,
        yuvSizes = listOf(1280 to 720, 960 to 720),
        jpegSizes = listOf(4000 to 3000, 1920 to 1080),
        flashAvailable = true,
        manualSensor = true,
    )

    @Test
    fun supportedManualSettingsPassThrough() {
        val s = CameraSettings(
            exposureMode = ExposureMode.MANUAL, iso = 800, exposureNs = 20_000_000L,
            focusMode = FocusMode.MANUAL, focusDiopters = 2f, awbMode = AwbMode.DAYLIGHT,
            linearZoom = 0.5f, analysisSize = 960 to 720, stillSize = 1920 to 1080, torch = true,
        )
        assertThat(sanitizeSettings(s, fullCaps())).isEqualTo(s)
    }

    @Test
    fun unknownCapabilitiesForceAutoDefaults() {
        val s = CameraSettings(
            exposureMode = ExposureMode.MANUAL, iso = 99999, exposureNs = 9_000_000_000L,
            evSteps = 99, focusMode = FocusMode.MANUAL, focusDiopters = 99f,
            awbMode = AwbMode.CLOUDY, linearZoom = 0.9f,
            analysisSize = 1 to 1, stillSize = 1 to 1, torch = true,
        )
        val clean = sanitizeSettings(s, CameraCapabilities())
        assertThat(clean.exposureMode).isEqualTo(ExposureMode.AUTO)
        assertThat(clean.focusMode).isEqualTo(FocusMode.AUTO)
        assertThat(clean.evSteps).isEqualTo(0)
        assertThat(clean.linearZoom).isEqualTo(0f)
        assertThat(clean.torch).isFalse()
        assertThat(clean.stillSize).isNull()
        assertThat(clean.analysisSize).isEqualTo(1280 to 720)
    }

    @Test
    fun valuesClampIntoRanges() {
        val clean = sanitizeSettings(
            CameraSettings(
                exposureMode = ExposureMode.MANUAL, iso = 99999, exposureNs = 500_000_000L,
                evSteps = -99, focusMode = FocusMode.MANUAL, focusDiopters = 99f,
            ),
            fullCaps(),
        )
        assertThat(clean.iso).isEqualTo(3200)
        assertThat(clean.exposureNs).isEqualTo(100_000_000L)
        assertThat(clean.evSteps).isEqualTo(-20)
        assertThat(clean.focusDiopters).isEqualTo(10f)
    }

    @Test
    fun fixedFocusCameraStaysAuto() {
        val caps = fullCaps().copy(afModes = setOf(CameraMetadata.CONTROL_AF_MODE_OFF), minFocusDistance = 0f)
        val clean = sanitizeSettings(CameraSettings(focusMode = FocusMode.MANUAL), caps)
        assertThat(clean.focusMode).isEqualTo(FocusMode.AUTO)
    }

    @Test
    fun unsupportedAwbFallsBackToAuto() {
        val caps = fullCaps().copy(awbModes = setOf(CameraMetadata.CONTROL_AWB_MODE_AUTO))
        val clean = sanitizeSettings(CameraSettings(awbMode = AwbMode.CLOUDY), caps)
        assertThat(clean.awbMode).isEqualTo(AwbMode.AUTO)
    }

    @Test
    fun missingAnalysisSizeFallsBackInOrder() {
        val caps = fullCaps().copy(yuvSizes = listOf(960 to 720))
        val clean = sanitizeSettings(CameraSettings(analysisSize = 1920 to 1080), caps)
        assertThat(clean.analysisSize).isEqualTo(960 to 720)
    }

    @Test
    fun serializeRoundTrip() {
        val s = CameraSettings(
            exposureMode = ExposureMode.MANUAL, iso = 800, exposureNs = 20_000_000L, evSteps = -3,
            focusMode = FocusMode.MANUAL, focusDiopters = 2.5f, awbMode = AwbMode.DAYLIGHT,
            linearZoom = 0.25f, analysisSize = 960 to 720, stillSize = 1920 to 1080, torch = true,
        )
        assertThat(parseSettings(serializeSettings(s))).isEqualTo(s)
    }

    @Test
    fun malformedPayloadDecodesNull() {
        assertThat(parseSettings(null)).isNull()
        assertThat(parseSettings("")).isNull()
        assertThat(parseSettings("v1;AUTO")).isNull()
        assertThat(parseSettings(serializeSettings(CameraSettings()).replace("v1", "v2"))).isNull()
        assertThat(parseSettings(serializeSettings(CameraSettings()).replace("1280x720", "0x0"))).isNull()
    }

    @Test
    fun formatExposureReadable() {
        assertThat(formatExposure(20_000_000L)).isEqualTo("1/50")
        assertThat(formatExposure(500_000_000L)).isEqualTo("500 ms")
        assertThat(formatExposure(2_000_000_000L)).isEqualTo("2.0 s")
        assertThat(formatExposure(0L)).isEqualTo("—")
    }

    @Test
    fun formatFocusReadable() {
        assertThat(formatFocus(0f)).isEqualTo("∞")
        assertThat(formatFocus(2f)).isEqualTo("50 cm")
        assertThat(formatFocus(0.5f)).isEqualTo("2.0 m")
        assertThat(formatFocus(-1f)).isEqualTo("—")
    }

    @Test
    fun exposureSliderRoundTrip() {
        val min = 1_000L
        val max = 100_000_000L
        assertThat(exposureSliderToNs(0f, min, max)).isEqualTo(min)
        assertThat(exposureSliderToNs(1f, min, max)).isEqualTo(max)
        val mid = exposureSliderToNs(0.5f, min, max)
        assertThat(mid).isGreaterThan(min)
        assertThat(mid).isLessThan(max)
        assertThat(exposureNsToSlider(mid, min, max)).isWithin(0.01f).of(0.5f)
    }

    @Test
    fun formatAppliedShowsRequestVsEffective() {
        val line = formatApplied(
            AppliedSettings(
                exposureNs = 20_000_000L, iso = 400, focusDiopters = null,
                aeMode = CameraMetadata.CONTROL_AE_MODE_ON,
                afMode = CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE,
                awbMode = CameraMetadata.CONTROL_AWB_MODE_AUTO, evSteps = 3,
            ),
        )
        assertThat(line).contains("1/50")
        assertThat(line).contains("ISO400")
        assertThat(line).contains("cont-p")
        assertThat(formatApplied(AppliedSettings(null, null, null, null, null, null, null))).isEqualTo("—")
    }

    private fun manualCaps() = CameraCapabilities(
        aeModes = setOf(
            CameraMetadata.CONTROL_AE_MODE_OFF,
            CameraMetadata.CONTROL_AE_MODE_ON,
        ),
        isoRange = 50..3200,
        exposureNsRange = 1_000L..100_000_000L,
        afModes = setOf(
            CameraMetadata.CONTROL_AF_MODE_OFF,
            CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE,
        ),
        minFocusDistance = 10f,
        awbModes = setOf(
            CameraMetadata.CONTROL_AWB_MODE_AUTO,
            CameraMetadata.CONTROL_AWB_MODE_DAYLIGHT,
        ),
        evStepsRange = -20..20,
        evStepSize = 0.1f,
        manualSensor = true,
    )

    @Test
    fun stillOptionsMirrorRuntimeKeysInManual() {
        val opts = stillOptionsFor(
            CameraSettings(
                exposureMode = ExposureMode.MANUAL,
                iso = 800,
                exposureNs = 40_000_000L,
                focusMode = FocusMode.MANUAL,
                focusDiopters = 2f,
                awbMode = AwbMode.DAYLIGHT,
            ),
            manualCaps(),
        )
        assertThat(opts.captureMode).isEqualTo(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
        assertThat(opts.aeMode).isEqualTo(CameraMetadata.CONTROL_AE_MODE_OFF)
        assertThat(opts.iso).isEqualTo(800)
        assertThat(opts.exposureNs).isEqualTo(40_000_000L)
        assertThat(opts.afMode).isEqualTo(CameraMetadata.CONTROL_AF_MODE_OFF)
        assertThat(opts.focusDiopters).isEqualTo(2f)
        assertThat(opts.awbMode).isEqualTo(CameraMetadata.CONTROL_AWB_MODE_DAYLIGHT)
        assertThat(opts.evSteps).isEqualTo(0)
    }

    @Test
    fun stillOptionsAutoKeepsQualityMode() {
        val opts = stillOptionsFor(CameraSettings(), manualCaps())
        assertThat(opts.captureMode).isEqualTo(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
        assertThat(opts.aeMode).isEqualTo(CameraMetadata.CONTROL_AE_MODE_ON)
        assertThat(opts.iso).isNull()
        assertThat(opts.exposureNs).isNull()
        assertThat(opts.afMode).isEqualTo(CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        assertThat(opts.evSteps).isEqualTo(0)
    }

    @Test
    fun stillOptionsUnsupportedFallsBack() {
        val opts = stillOptionsFor(
            CameraSettings(
                exposureMode = ExposureMode.MANUAL,
                focusMode = FocusMode.MANUAL,
                awbMode = AwbMode.DAYLIGHT,
            ),
            CameraCapabilities(),
        )
        assertThat(opts.captureMode).isEqualTo(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
        assertThat(opts.aeMode).isEqualTo(CameraMetadata.CONTROL_AE_MODE_ON)
        assertThat(opts.afMode).isNull()
        assertThat(opts.awbMode).isNull()
        assertThat(opts.evSteps).isEqualTo(0)
    }

    @Test
    fun stillOptionsCarryEvSteps() {
        val opts = stillOptionsFor(
            CameraSettings(evSteps = 17),
            manualCaps(),
        )
        assertThat(opts.evSteps).isEqualTo(17)
    }

    @Test
    fun manualWithoutManualSensorFallsBack() {
        val caps = manualCaps().copy(manualSensor = false)
        assertThat(caps.supportsManualExposure).isFalse()
        val clean = sanitizeSettings(
            CameraSettings(exposureMode = ExposureMode.MANUAL, iso = 800),
            caps,
        )
        assertThat(clean.exposureMode).isEqualTo(ExposureMode.AUTO)
        val opts = stillOptionsFor(
            CameraSettings(exposureMode = ExposureMode.MANUAL),
            caps,
        )
        assertThat(opts.captureMode).isEqualTo(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
        assertThat(opts.aeMode).isEqualTo(CameraMetadata.CONTROL_AE_MODE_ON)
        assertThat(opts.iso).isNull()
    }

    @Test
    fun aspectBucketSplits16by9And4by3() {
        assertThat(aspectBucketFor(1920, 1080)).isEqualTo(AspectBucket.R16_9)
        assertThat(aspectBucketFor(1280, 720)).isEqualTo(AspectBucket.R16_9)
        assertThat(aspectBucketFor(3840, 2160)).isEqualTo(AspectBucket.R16_9)
        assertThat(aspectBucketFor(4624, 3468)).isEqualTo(AspectBucket.R4_3)
        assertThat(aspectBucketFor(960, 720)).isEqualTo(AspectBucket.R4_3)
        assertThat(aspectBucketFor(640, 480)).isEqualTo(AspectBucket.R4_3)
        assertThat(aspectBucketFor(1088, 1088)).isEqualTo(AspectBucket.R4_3)
    }
}
