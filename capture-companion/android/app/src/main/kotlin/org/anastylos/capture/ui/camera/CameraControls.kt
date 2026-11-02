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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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

/** The settings this camera can actually change, in display order. */
fun availableWheels(settings: CameraSettings, caps: CameraCapabilities): List<WheelKind> = buildList {
    if (caps.supportsManualExposure) {
        add(WheelKind.ISO)
        add(WheelKind.SPEED)
    }
    if (caps.supportsEv && settings.exposureMode == ExposureMode.AUTO) add(WheelKind.EV)
    if (caps.supportsManualFocus) add(WheelKind.FOCUS)
    if (caps.awbModes.isNotEmpty()) add(WheelKind.WB)
}

/** Plain icon, no backdrop: the bars around the picture carry the contrast. */
@Composable
fun BarIconButton(glyph: Glyph, description: String, tint: Color = Palette.Paper, filled: Boolean = false, onClick: () -> Unit) {
    Box(
        Modifier.size(48.dp).clickable(onClickLabel = description, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { GlyphIcon(glyph, tint, filled = filled) }
}

/** Soft dark fade that lets the image show through; never a box. */
fun Modifier.imageFade(): Modifier =
    background(Brush.verticalGradient(listOf(Color.Transparent, Color(0xB3000000))))

/** ISO · SPEED · EV · FOCUS · WB across the full width; tapping one opens its ruler. */
@Composable
fun SettingsReadout(settings: CameraSettings, caps: CameraCapabilities, active: WheelKind?, onSelect: (WheelKind) -> Unit) {
    val wheels = availableWheels(settings, caps)
    if (wheels.isEmpty()) return
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
        wheels.forEach { kind ->
            Column(
                Modifier.clickable { onSelect(kind) }.padding(horizontal = 6.dp, vertical = 10.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(kind.label, color = if (kind == active) Palette.Paper else Palette.Ochre, fontSize = 12.sp)
                Text(readoutValue(kind, settings, caps), color = if (kind == active) Palette.Paper else Palette.Ochre, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

private fun readoutValue(kind: WheelKind, settings: CameraSettings, caps: CameraCapabilities): String {
    val manualExposure = settings.exposureMode == ExposureMode.MANUAL
    return when (kind) {
        WheelKind.ISO -> if (manualExposure) settings.iso.toString() else "AUTO"
        WheelKind.SPEED -> if (manualExposure) formatExposure(settings.exposureNs) else "AUTO"
        WheelKind.EV -> "%+.1f".format(java.util.Locale.US, settings.evSteps * (caps.evStepSize ?: 0f))
        WheelKind.FOCUS -> if (settings.focusMode == FocusMode.MANUAL) formatFocus(settings.focusDiopters) else "AUTO"
        WheelKind.WB -> settings.awbMode.label.uppercase()
    }
}

/** The ruler for one setting over a fading band: (i) top left, Auto/Manual tag left of the ruler. */
@Composable
fun SettingsWheelPanel(
    kind: WheelKind,
    settings: CameraSettings,
    caps: CameraCapabilities,
    onUpdate: Update,
    onInfo: (HelpTopic) -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(bottom = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Box(Modifier.padding(start = 12.dp).size(40.dp).clickable(onClickLabel = "Explain ${kind.label}") { onInfo(kind.help) }, contentAlignment = Alignment.Center) {
            GlyphIcon(Glyph.Info, Palette.Paper, size = 22.dp)
        }
        when (kind) {
            WheelKind.ISO -> ExposureRuler(settings, caps, onUpdate, isoScale(caps), settings.iso) { v -> onUpdate { it.copy(iso = v) } }
            WheelKind.SPEED -> ExposureRuler(settings, caps, onUpdate, shutterScale(caps), settings.exposureNs) { v -> onUpdate { it.copy(exposureNs = v) } }
            WheelKind.EV -> EvRuler(settings, caps, onUpdate)
            WheelKind.FOCUS -> FocusRuler(settings, caps, onUpdate)
            WheelKind.WB -> WhiteBalanceStrip(settings, caps, onUpdate)
        }
    }
}

@Composable
private fun <T> ExposureRuler(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update, scale: WheelScale<T>?, value: T, commit: (T) -> Unit) {
    val manual = settings.exposureMode == ExposureMode.MANUAL
    ModeRow(
        tag = if (manual) "MANUAL" else "AUTO",
        tagEnabled = caps.supportsManualExposure,
        onToggle = { onUpdate { it.copy(exposureMode = if (manual) ExposureMode.AUTO else ExposureMode.MANUAL) } },
    ) {
        if (manual && scale != null) ContinuousWheel(scale, value, onCommit = commit)
        else Text("Tap AUTO to adjust by hand", color = Palette.Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
    }
}

@Composable
private fun EvRuler(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val scale = evScale(caps) ?: return
    ModeRow(tag = "EV", tagEnabled = false, onToggle = {}) {
        ContinuousWheel(scale, settings.evSteps, onCommit = { v -> onUpdate { it.copy(evSteps = v) } })
    }
}

@Composable
private fun FocusRuler(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val manual = settings.focusMode == FocusMode.MANUAL
    val scale = focusScale(caps)
    ModeRow(
        tag = if (manual) "MANUAL" else "AUTO",
        tagEnabled = caps.supportsManualFocus,
        onToggle = { onUpdate { it.copy(focusMode = if (manual) FocusMode.AUTO else FocusMode.MANUAL) } },
    ) {
        if (manual && scale != null) ContinuousWheel(scale, settings.focusDiopters, onCommit = { v -> onUpdate { it.copy(focusDiopters = v) } })
        else Text("Tap AUTO to adjust by hand", color = Palette.Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
    }
}

@Composable
private fun ModeRow(tag: String, tagEnabled: Boolean, onToggle: () -> Unit, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.Bottom) {
        Text(
            tag,
            color = if (tagEnabled) Palette.Paper else Palette.Muted,
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier
                .padding(bottom = 6.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(Color(0x99000000))
                .clickable(enabled = tagEnabled, onClick = onToggle)
                .padding(horizontal = 8.dp, vertical = 5.dp),
        )
        Box(Modifier.weight(1f)) { content() }
        Box(Modifier.width(52.dp))
    }
}

@Composable
private fun WhiteBalanceStrip(settings: CameraSettings, caps: CameraCapabilities, onUpdate: Update) {
    val modes = AwbMode.entries.filter { it.camera2 in caps.awbModes }
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
        modes.forEach { mode ->
            val selected = mode == settings.awbMode
            Text(
                mode.label,
                color = if (selected) Palette.Ochre else Palette.Paper.copy(alpha = 0.8f),
                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                fontSize = 15.sp,
                modifier = Modifier.clickable { onUpdate { it.copy(awbMode = mode) } }.padding(10.dp),
            )
        }
    }
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
                .background(Palette.Ground.copy(alpha = 0.96f))
                .verticalScroll(rememberScrollState())
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("More", style = MaterialTheme.typography.headlineSmall, color = Palette.Paper, modifier = Modifier.weight(1f))
                BarIconButton(Glyph.Info, "Explain this menu") { onInfo(HelpTopic.Menu) }
                BarIconButton(Glyph.Close, "Close menu", onClick = onClose)
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
