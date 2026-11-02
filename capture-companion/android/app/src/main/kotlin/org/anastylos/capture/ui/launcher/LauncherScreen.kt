package org.anastylos.capture.ui.launcher

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.anastylos.capture.ui.theme.Palette

private const val ASSEMBLY_MS = 2600
private const val HOLD_MS = 700L
private const val FRAGMENT_START = 0.06f
private const val FRAGMENT_STAGGER = 0.07f
private const val FRAGMENT_SPAN = 0.5f
private const val REVEAL_START = 0.72f
private const val REVEAL_SPAN = 0.2f

/**
 * Launch animation: the mark starts as scattered fragments and is
 * reassembled from the base up, then the name and claim fade in
 * (the website hero, played once instead of scrubbed by scroll).
 */
@Composable
fun LauncherScreen(onFinished: () -> Unit) {
    val progress = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        progress.animateTo(1f, tween(ASSEMBLY_MS, easing = LinearEasing))
        delay(HOLD_MS)
        onFinished()
    }
    val paths = remember {
        LOGO_FRAGMENTS.map { PathParser().parsePathString(it.pathData).toPath() }
    }
    val reveal = ((progress.value - REVEAL_START) / REVEAL_SPAN).coerceIn(0f, 1f)
    Column(
        modifier = Modifier.fillMaxSize().background(Palette.Ground).padding(horizontal = 48.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Canvas(Modifier.fillMaxWidth(0.62f).aspectRatio(LOGO_WIDTH / LOGO_HEIGHT)) {
            val scale = size.width / LOGO_WIDTH
            LOGO_FRAGMENTS.forEachIndexed { index, fragment ->
                val local = ((progress.value - FRAGMENT_START - index * FRAGMENT_STAGGER) / FRAGMENT_SPAN).coerceIn(0f, 1f)
                val rest = 1f - FastOutSlowInEasing.transform(local)
                translate(
                    left = fragment.scatterX * rest * size.width,
                    top = fragment.scatterY * rest * size.width,
                ) {
                    rotate(
                        degrees = fragment.scatterDeg * rest,
                        pivot = Offset(fragment.centroidX * scale, fragment.centroidY * scale),
                    ) {
                        drawScaledPath(paths[index], scale)
                    }
                }
            }
        }
        Text(
            "Anastylos",
            style = MaterialTheme.typography.headlineLarge,
            color = Palette.Paper,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 36.dp).alpha(reveal),
        )
        Text(
            "A workbench that remembers what you took apart.",
            style = MaterialTheme.typography.bodyLarge,
            color = Palette.Muted,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 12.dp).alpha(reveal),
        )
    }
}

private fun DrawScope.drawScaledPath(path: Path, factor: Float) {
    scale(factor, factor, pivot = Offset.Zero) {
        drawPath(path, Palette.Paper)
    }
}
