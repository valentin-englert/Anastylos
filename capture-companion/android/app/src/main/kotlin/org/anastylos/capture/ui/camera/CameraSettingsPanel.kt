package org.anastylos.capture.ui.camera

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.anastylos.capture.camera.AwbMode
import org.anastylos.capture.camera.CameraCapabilities
import org.anastylos.capture.camera.CameraSettings
import org.anastylos.capture.camera.ExposureMode
import org.anastylos.capture.camera.FocusMode
import org.anastylos.capture.camera.exposureNsToSlider
import org.anastylos.capture.camera.exposureSliderToNs
import org.anastylos.capture.camera.formatExposure
import org.anastylos.capture.camera.formatFocus

/**
 * Minimal manual-control panel shared by Live Observer and Guided
 * Capture. Every control renders only when the bound camera
 * supports it ([CameraCapabilities]); unsupported controls are
 * absent, never silently ignored. Sliders commit on release to
 * avoid persistence spam while dragging.
 */
@Composable
fun CameraSettingsPanel(
    settings: CameraSettings,
    caps: CameraCapabilities,
    profiles: List<String>,
    activeProfile: String,
    appliedLine: String?,
    settingsNote: String?,
    analysisSelectable: Boolean,
    onUpdate: ((CameraSettings) -> CameraSettings) -> Unit,
    onProfile: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Settings", style = MaterialTheme.typography.titleSmall)
        ChipRow {
            profiles.forEach { name ->
                FilterChip(
                    selected = name == activeProfile,
                    onClick = { onProfile(name) },
                    label = { Text(name) },
                )
            }
        }
        ExposureSection(settings, caps, onUpdate)
        FocusSection(settings, caps, onUpdate)
        AwbSection(settings, caps, onUpdate)
        if (caps.supportsZoom) {
            var local by remember(settings.linearZoom) { mutableFloatStateOf(settings.linearZoom) }
            Text("Zoom ${(local * 100).toInt()}%", style = MaterialTheme.typography.bodySmall)
            Slider(
                value = local,
                onValueChange = { local = it },
                onValueChangeFinished = { onUpdate { it.copy(linearZoom = local) } },
                valueRange = 0f..1f,
            )
        }
        if (analysisSelectable) {
            val options = listOf(1920 to 1080, 1280 to 720, 960 to 720, 640 to 480)
                .filter { it in caps.yuvSizes }
            if (options.isNotEmpty()) {
                Text("Analysis", style = MaterialTheme.typography.bodySmall)
                ChipRow {
                    options.forEach { size ->
                        FilterChip(
                            selected = size == settings.analysisSize,
                            onClick = { onUpdate { it.copy(analysisSize = size) } },
                            label = { Text("${size.first}×${size.second}") },
                        )
                    }
                }
            }
        }
        if (caps.jpegSizes.isNotEmpty()) {
            Text("Still", style = MaterialTheme.typography.bodySmall)
            ChipRow {
                FilterChip(
                    selected = settings.stillSize == null,
                    onClick = { onUpdate { it.copy(stillSize = null) } },
                    label = { Text("Max") },
                )
                val options = listOf(1920 to 1080, 1280 to 720, 960 to 720, 640 to 480)
                    .filter { it in caps.jpegSizes }
                options.forEach { size ->
                    FilterChip(
                        selected = settings.stillSize == size,
                        onClick = { onUpdate { it.copy(stillSize = size) } },
                        label = { Text("${size.first}×${size.second}") },
                    )
                }
            }
        }
        if (caps.flashAvailable) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Torch", style = MaterialTheme.typography.bodySmall)
                Switch(
                    checked = settings.torch,
                    onCheckedChange = { checked -> onUpdate { it.copy(torch = checked) } },
                )
            }
        }
        Text(
            "Applied: ${appliedLine ?: "n/a"}",
            style = MaterialTheme.typography.bodySmall,
        )
        settingsNote?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun ExposureSection(
    settings: CameraSettings,
    caps: CameraCapabilities,
    onUpdate: ((CameraSettings) -> CameraSettings) -> Unit,
) {
    Text("Exposure", style = MaterialTheme.typography.bodySmall)
    ChipRow {
        FilterChip(
            selected = settings.exposureMode == ExposureMode.AUTO,
            onClick = { onUpdate { it.copy(exposureMode = ExposureMode.AUTO) } },
            label = { Text("Auto") },
        )
        if (caps.supportsManualExposure) {
            FilterChip(
                selected = settings.exposureMode == ExposureMode.MANUAL,
                onClick = { onUpdate { it.copy(exposureMode = ExposureMode.MANUAL) } },
                label = { Text("Manual") },
            )
        }
    }
    if (!caps.supportsManualExposure) {
        Text(
            "Manual unavailable on this camera (no manual-sensor HAL support)",
            style = MaterialTheme.typography.bodySmall,
        )
    }
    val isoRange = caps.isoRange
    val expRange = caps.exposureNsRange
    if (settings.exposureMode == ExposureMode.MANUAL && isoRange != null && expRange != null) {
        val maxNs = minOf(expRange.endInclusive, caps.maxFrameDurationNs ?: expRange.endInclusive)
        var isoLocal by remember(settings.iso) { mutableFloatStateOf(settings.iso.toFloat()) }
        Text("ISO ${isoLocal.toInt()}", style = MaterialTheme.typography.bodySmall)
        Slider(
            value = isoLocal,
            onValueChange = { isoLocal = it },
            onValueChangeFinished = { onUpdate { it.copy(iso = isoLocal.toInt()) } },
            valueRange = isoRange.start.toFloat()..isoRange.endInclusive.toFloat(),
        )
        var expLocal by remember(settings.exposureNs) {
            mutableFloatStateOf(exposureNsToSlider(settings.exposureNs, expRange.start, maxNs))
        }
        Text(
            "Shutter ${formatExposure(exposureSliderToNs(expLocal, expRange.start, maxNs))}",
            style = MaterialTheme.typography.bodySmall,
        )
        Slider(
            value = expLocal,
            onValueChange = { expLocal = it },
            onValueChangeFinished = {
                onUpdate { it.copy(exposureNs = exposureSliderToNs(expLocal, expRange.start, maxNs)) }
            },
            valueRange = 0f..1f,
        )
    }
    val evRange = caps.evStepsRange
    val evStep = caps.evStepSize
    if (settings.exposureMode == ExposureMode.AUTO && evRange != null && evStep != null) {
        var evLocal by remember(settings.evSteps) { mutableFloatStateOf(settings.evSteps.toFloat()) }
        Text(
            "EV %+.1f".format(java.util.Locale.US, evLocal * evStep),
            style = MaterialTheme.typography.bodySmall,
        )
        Slider(
            value = evLocal,
            onValueChange = { evLocal = it },
            onValueChangeFinished = { onUpdate { it.copy(evSteps = evLocal.toInt()) } },
            valueRange = evRange.start.toFloat()..evRange.endInclusive.toFloat(),
        )
    }
}

@Composable
private fun FocusSection(
    settings: CameraSettings,
    caps: CameraCapabilities,
    onUpdate: ((CameraSettings) -> CameraSettings) -> Unit,
) {
    if (!caps.supportsAutoFocusModes && !caps.supportsManualFocus) return
    Text("Focus", style = MaterialTheme.typography.bodySmall)
    ChipRow {
        FilterChip(
            selected = settings.focusMode == FocusMode.AUTO,
            onClick = { onUpdate { it.copy(focusMode = FocusMode.AUTO) } },
            label = { Text("Auto") },
        )
        if (caps.supportsManualFocus) {
            FilterChip(
                selected = settings.focusMode == FocusMode.MANUAL,
                onClick = { onUpdate { it.copy(focusMode = FocusMode.MANUAL) } },
                label = { Text("Manual") },
            )
        }
    }
    val minFocus = caps.minFocusDistance
    if (settings.focusMode == FocusMode.MANUAL && minFocus != null && minFocus > 0f) {
        var focLocal by remember(settings.focusDiopters) {
            mutableFloatStateOf(settings.focusDiopters)
        }
        Text(
            "Distance ${formatFocus(focLocal)}",
            style = MaterialTheme.typography.bodySmall,
        )
        Slider(
            value = focLocal,
            onValueChange = { focLocal = it },
            onValueChangeFinished = { onUpdate { it.copy(focusDiopters = focLocal) } },
            valueRange = 0f..minFocus,
        )
    }
}

@Composable
private fun AwbSection(
    settings: CameraSettings,
    caps: CameraCapabilities,
    onUpdate: ((CameraSettings) -> CameraSettings) -> Unit,
) {
    val modes = AwbMode.entries.filter { it.camera2 in caps.awbModes }
    if (modes.isEmpty()) return
    Text("White balance", style = MaterialTheme.typography.bodySmall)
    ChipRow {
        modes.forEach { mode ->
            FilterChip(
                selected = mode == settings.awbMode,
                onClick = { onUpdate { it.copy(awbMode = mode) } },
                label = { Text(mode.label) },
            )
        }
    }
}

@Composable
private fun ChipRow(content: @Composable () -> Unit) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
    ) {
        content()
    }
}
