package org.anastylos.capture.ui.camera

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.dp
import org.anastylos.capture.ui.theme.Glyph
import org.anastylos.capture.ui.theme.Palette

/** Keeps overlay text readable on any live image without a box behind it. */
fun TextStyle.onImage(): TextStyle = copy(shadow = Shadow(Color.Black, Offset(0f, 2f), 8f))

/**
 * Shared camera screen frame. The picture sits between solid bars, so the
 * controls are separated from the image by a straight line: bars above and
 * below in portrait, a side bar in landscape (where only the essentials show).
 */
@Composable
fun CameraScaffold(
    onBack: () -> Unit,
    status: @Composable (compact: Boolean) -> Unit,
    barExtras: @Composable () -> Unit,
    torchOn: Boolean,
    torchAvailable: Boolean,
    onTorch: () -> Unit,
    onMenu: () -> Unit,
    actions: @Composable (compact: Boolean) -> Unit,
    overlayTop: @Composable ColumnScope.(landscape: Boolean) -> Unit,
    overlayBottom: @Composable ColumnScope.(landscape: Boolean) -> Unit,
    preview: @Composable BoxScope.() -> Unit,
) {
    val landscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE
    val pictureArea: @Composable BoxScope.() -> Unit = {
        preview()
        Column(Modifier.align(Alignment.TopStart).fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            overlayTop(landscape)
        }
        Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().imageFade()) { overlayBottom(landscape) }
    }
    if (landscape) {
        Row(Modifier.fillMaxSize().background(Palette.Ground).windowInsetsPadding(WindowInsets.safeDrawing)) {
            Box(Modifier.weight(1f).fillMaxHeight().clipToBounds(), content = pictureArea)
            Column(
                Modifier.width(104.dp).fillMaxHeight().padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                BarIconButton(Glyph.Back, "Back", onClick = onBack)
                status(true)
                barExtras()
                if (torchAvailable) BarIconButton(Glyph.Bolt, "Flashlight", tint = if (torchOn) Palette.Ochre else Palette.Paper, filled = torchOn, onClick = onTorch)
                BarIconButton(Glyph.Menu, "More settings", onClick = onMenu)
                Spacer(Modifier.weight(1f))
                actions(true)
            }
        }
    } else {
        Column(Modifier.fillMaxSize().background(Palette.Ground).windowInsetsPadding(WindowInsets.safeDrawing)) {
            Row(Modifier.fillMaxWidth().height(56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                BarIconButton(Glyph.Back, "Back", onClick = onBack)
                Box(Modifier.weight(1f), contentAlignment = Alignment.Center) { status(false) }
                barExtras()
                if (torchAvailable) BarIconButton(Glyph.Bolt, "Flashlight", tint = if (torchOn) Palette.Ochre else Palette.Paper, filled = torchOn, onClick = onTorch)
                BarIconButton(Glyph.Menu, "More settings", onClick = onMenu)
            }
            Box(Modifier.weight(1f).fillMaxWidth().clipToBounds(), content = pictureArea)
            Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) { actions(false) }
        }
    }
}
