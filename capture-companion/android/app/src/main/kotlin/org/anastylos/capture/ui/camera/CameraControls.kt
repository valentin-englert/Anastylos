package org.anastylos.capture.ui.camera

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.anastylos.capture.camera.AwbMode
import org.anastylos.capture.camera.CameraCapabilities
import org.anastylos.capture.camera.CameraSettings
import org.anastylos.capture.camera.ExposureMode
import org.anastylos.capture.camera.FocusMode
import org.anastylos.capture.camera.UsableCamera
import org.anastylos.capture.camera.formatExposure
import org.anastylos.capture.camera.formatFocus
import org.anastylos.capture.ui.onboarding.HelpTopic
import org.anastylos.capture.ui.theme.Glyph
import org.anastylos.capture.ui.theme.GlyphIcon
import org.anastylos.capture.ui.theme.Palette

enum class WheelKind(val label: String, val help: HelpTopic) {
    ISO("ISO", HelpTopic.Iso),
    SPEED("SPEED", HelpTopic.Speed),
    EV("EV", HelpTopic.Ev),
    FOCUS("FOCUS", HelpTopic.Focus),
    WB("WB", HelpTopic.WhiteBalance),
}

private typealias Update = ((CameraSettings) -> CameraSettings) -> Unit

/** Round icon button on a dark scrim, readable over any live image. */
@Composable
fun ScrimIconButton(glyph: Glyph, description: String, tint: Color = Palette.Paper, filled: Boolean = false, onClick: () -> Unit) {
    Box(
        Modifier
            .size(48.dp)
            .clip(CircleShape)
            .background(Palette.Scrim)
            .clickable(onClickLabel = description, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { GlyphIcon(glyph, tint, filled = filled) }
}

/** Flashlight switch and the menu button, top right of the camera. */
@Composable
fun CameraTopControls(torchOn: Boolean, torchAvailable: Boolean, onTorch: () -> Unit, onMenu: () -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
        if (torchAvailable) {
            ScrimIconButton(Glyph.Bolt, "Flashlight", tint = if (torchOn) Palette.Ochre else Palette.Paper, filled = torchOn, onClick = onTorch)
        }
        ScrimIconButton(Glyph.Menu, "More settings", onClick = onMenu)
    }
}

/** ISO · SPEED · EV · FOCUS · WB, tappable; each value opens its wheel. */
@Composable
fun SettingsReadout(settings: CameraSettings, caps: CameraCapabilities, active: WheelKind?, onSelect: (WheelKind) -> Unit) {
    val manualExposure = settings.exposureMode == ExposureMode.MANUAL
    val evStep = caps.evStepSize
    Row(
        Modifier
            .clip(RoundedCornerShape(24.dp))
            .background(Palette.Scrim)
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 14.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        if (caps.supportsManualExposure) {
            ReadoutToken(WheelKind.ISO, if (manualExposure) settings.iso.toString() else "AUTO", active, onSelect)
            ReadoutToken(WheelKind.SPEED, if (manualExposure) formatExposure(settings.exposureNs) else "AUTO", active, onSelect)
        }
        if (caps.supportsEv && evStep != null && !manualExposure) {
            ReadoutToken(WheelKind.EV, "%+.1f".format(java.util.Locale.US, settings.evSteps * evStep), active, onSelect)
        }
        if (caps.supportsManualFocus) {
            val focus = if (settings.focusMode == FocusMode.MANUAL) formatFocus(settings.focusDiopters) else "AUTO"
            ReadoutToken(WheelKind.FOCUS, focus, active, onSelect)
        }
        if (caps.awbModes.isNotEmpty()) ReadoutToken(WheelKind.WB, settings.awbMode.label.uppercase(), active, onSelect)
    }
}

@Composable
private fun ReadoutToken(kind: WheelKind, value: String, active: WheelKind?, onSelect: (WheelKind) -> Unit) {
    Row(
        Modifier.clickable { onSelect(kind) }.padding(horizontal = 8.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(kind.label, color = Palette.Muted, style = MaterialTheme.typography.bodyMedium)
        Text(
            value,
            color = if (kind == active) Palette.Paper else Palette.Ochre,
            fontWeight = FontWeight.Bold,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

/** The wheel for one setting with Auto / Manual above it and an (i) button top left. */
@Composable
fun SettingsWheelPanel(
    kind: WheelKind,
    settings: CameraSettings,
    caps: CameraCapabilities,
    onUpdate: Update,
    onInfo: (HelpTopic) -> Unit,
    onClose: () -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().background(Palette.Scrim).padding(top = 8.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).clickable(onClickLabel = "Explain ${kind.label}") { onInfo(kind.help) }, contentAlignment = Alignment.CenterStart) {
                GlyphIcon(Glyph.Info, Palette.Paper)
            }
            Box(Modifier.weight(1f))
            Box(Modifier.size(40.dp).clickable(onClickLabel = "Close") { onClose() }, contentAlignment = Alignment.CenterEnd) {
                GlyphIcon(Glyph.Close, Palette.Paper)
            }
        }
        when (kind) {
            WheelKind.ISO, WheelKind.SPEED -> ExposureWheel(kind, settings, caps, onUpdate)
            WheelKind.EV -> EvWheel(settings, caps, onUpdate)
            WheelKind.FOCUS -> FocusWheel(settings, caps, onUpdate)
            WheelKind.WB -> WhiteBalanceWheel(settings, caps, onUpdate)
        }
    }
}

@Composable
private fun AutoManualToggle(manual: Boolean, manualAvailable: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
        ModeOption("A", "AUTO", selected = !manual, enabled = true) { onChange(false) }
        ModeOption("M", "MANUAL", selected = manual, enabled = manualAvailable) { onChange(true) }
    }
}

@Composable
private fun ModeOption(letter: String, label: String, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val color = when {
        !enabled -> Palette.Muted.copy(alpha = 0.5f)
        selected -> Palette.Ochre
        else -> Palette.Paper
    }
    Column(
        Modifier.width(120.dp).clickable(enabled = enabled, onClick = onClick).padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(letter, color = color, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleLarge)
        Text(label, color = color, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun ExposureWheel(kind: WheelKind, settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val manual = settings.exposureMode == ExposureMode.MANUAL
    AutoManualToggle(manual, caps.supportsManualExposure) { wantManual ->
        onUpdate { it.copy(exposureMode = if (wantManual) ExposureMode.MANUAL else ExposureMode.AUTO) }
    }
    if (!manual) return
    if (kind == WheelKind.ISO) {
        val stops = isoStops(caps)
        ControlWheel(stops, nearestStop(stops, settings.iso), { stop -> onUpdate { it.copy(iso = stop.value) } })
    } else {
        val stops = shutterStops(caps)
        ControlWheel(stops, nearestStop(stops, settings.exposureNs), { stop -> onUpdate { it.copy(exposureNs = stop.value) } })
    }
}

@Composable
private fun EvWheel(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val stops = evStops(caps)
    ControlWheel(stops, nearestStop(stops, settings.evSteps), { stop -> onUpdate { it.copy(evSteps = stop.value) } })
}

@Composable
private fun FocusWheel(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val manual = settings.focusMode == FocusMode.MANUAL
    AutoManualToggle(manual, caps.supportsManualFocus) { wantManual ->
        onUpdate { it.copy(focusMode = if (wantManual) FocusMode.MANUAL else FocusMode.AUTO) }
    }
    if (!manual) return
    val stops = focusStops(caps)
    ControlWheel(stops, nearestStop(stops, settings.focusDiopters), { stop -> onUpdate { it.copy(focusDiopters = stop.value) } })
}

@Composable
private fun WhiteBalanceWheel(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val stops = AwbMode.entries.filter { it.camera2 in caps.awbModes }.map { WheelStop(it.label, it) }
    val index = stops.indexOfFirst { it.value == settings.awbMode }.coerceAtLeast(0)
    ControlWheel(stops, index, { stop -> onUpdate { it.copy(awbMode = stop.value) } })
}

/** Right-hand sandwich menu: everything that is not needed during a shot. */
@Composable
fun CameraMenu(
    cameras: List<UsableCamera>,
    effectiveCameraId: String?,
    cameraNote: String?,
    onSelectCamera: (String) -> Unit,
    profiles: List<String>,
    activeProfile: String,
    onProfile: (String) -> Unit,
    settings: CameraSettings,
    caps: CameraCapabilities,
    onUpdate: Update,
    appliedLine: String?,
    settingsNote: String?,
    analysisSelectable: Boolean,
    onInfo: (HelpTopic) -> Unit,
    onClose: () -> Unit,
    extra: @Composable ColumnScope.() -> Unit = {},
) {
    Row(Modifier.fillMaxWidth().fillMaxHeight()) {
        Box(Modifier.weight(1f).fillMaxHeight().clickable(onClickLabel = "Close menu") { onClose() })
        Column(
            Modifier
                .width(320.dp)
                .fillMaxHeight()
                .background(Palette.Ground.copy(alpha = 0.94f))
                .verticalScroll(rememberScrollState())
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("More", style = MaterialTheme.typography.headlineSmall, color = Palette.Paper, modifier = Modifier.weight(1f))
                Box(Modifier.size(40.dp).clickable(onClickLabel = "Explain this menu") { onInfo(HelpTopic.Menu) }, contentAlignment = Alignment.Center) {
                    GlyphIcon(Glyph.Info, Palette.Paper)
                }
                Box(Modifier.size(40.dp).clickable(onClickLabel = "Close menu") { onClose() }, contentAlignment = Alignment.Center) {
                    GlyphIcon(Glyph.Close, Palette.Paper)
                }
            }
            extra()
            if (cameras.isNotEmpty()) {
                MenuSection("Camera") {
                    cameras.forEach { camera ->
                        OptionRow(camera.label, camera.id == effectiveCameraId, Glyph.Switch) { onSelectCamera(camera.id) }
                    }
                    cameraNote?.let { Text(it, color = Palette.Muted, style = MaterialTheme.typography.bodySmall) }
                }
            }
            MenuSection("Profile") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                    profiles.forEach { name -> Pill(name, name == activeProfile) { onProfile(name) } }
                }
            }
            if (caps.supportsZoom) {
                MenuSection("Zoom") {
                    var local by remember(settings.linearZoom) { mutableFloatStateOf(settings.linearZoom) }
                    Slider(
                        value = local,
                        onValueChange = { local = it },
                        onValueChangeFinished = { onUpdate { it.copy(linearZoom = local) } },
                        colors = SliderDefaults.colors(thumbColor = Palette.Ochre, activeTrackColor = Palette.Ochre),
                    )
                }
            }
            if (analysisSelectable) {
                val options = listOf(1920 to 1080, 1280 to 720, 960 to 720, 640 to 480).filter { it in caps.yuvSizes }
                if (options.isNotEmpty()) {
                    MenuSection("Analysis size") {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                            options.forEach { size ->
                                Pill("${size.first}×${size.second}", size == settings.analysisSize) { onUpdate { it.copy(analysisSize = size) } }
                            }
                        }
                    }
                }
            }
            if (caps.jpegSizes.isNotEmpty()) {
                MenuSection("Photo size") {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                        Pill("Max", settings.stillSize == null) { onUpdate { it.copy(stillSize = null) } }
                        listOf(1920 to 1080, 1280 to 720, 960 to 720, 640 to 480).filter { it in caps.jpegSizes }.forEach { size ->
                            Pill("${size.first}×${size.second}", settings.stillSize == size) { onUpdate { it.copy(stillSize = size) } }
                        }
                    }
                }
            }
            appliedLine?.let { Text("Applied: $it", color = Palette.Muted, style = MaterialTheme.typography.bodySmall) }
            settingsNote?.let { Text(it, color = Palette.Muted, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
fun MenuSection(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title.uppercase(), color = Palette.Muted, style = MaterialTheme.typography.titleSmall)
        content()
    }
}

@Composable
fun Pill(text: String, selected: Boolean, onClick: () -> Unit) {
    Text(
        text,
        color = if (selected) Palette.Ground else Palette.Paper,
        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
        style = MaterialTheme.typography.bodyMedium,
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(if (selected) Palette.Ochre else Palette.SurfaceRaised)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
    )
}

@Composable
private fun OptionRow(label: String, selected: Boolean, glyph: Glyph, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(if (selected) Palette.SurfaceRaised else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        GlyphIcon(glyph, if (selected) Palette.Ochre else Palette.Muted)
        Text(label, color = if (selected) Palette.Ochre else Palette.Paper, style = MaterialTheme.typography.bodyLarge)
    }
}
