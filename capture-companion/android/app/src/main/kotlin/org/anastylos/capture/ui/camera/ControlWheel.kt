package org.anastylos.capture.ui.camera

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.anastylos.capture.ui.theme.Palette
import kotlin.math.abs
import kotlin.math.roundToInt

/** How many screen widths of drag cover the whole range. */
private const val RULER_SCREENS = 2.5f
private const val MAJOR_EVERY = 5

/**
 * Stepless setting ruler: drag left or right and the value follows
 * the finger; the ochre marker stays fixed in the middle. The value
 * is committed when the finger lifts.
 */
@Composable
fun <T> ContinuousWheel(
    scale: WheelScale<T>,
    value: T,
    onCommit: (T) -> Unit,
    modifier: Modifier = Modifier,
) {
    var fraction by remember(value) { mutableFloatStateOf(scale.toFraction(value)) }
    val haptic = LocalHapticFeedback.current
    val currentScale by rememberUpdatedState(scale)
    val currentCommit by rememberUpdatedState(onCommit)
    var lastTick by remember { mutableFloatStateOf(Float.NaN) }
    Column(modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            scale.format(scale.fromFraction(fraction)),
            color = Palette.Ochre,
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
        )
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(44.dp)
                .pointerInput(Unit) {
                    val tickPx = 10.dp.toPx()
                    detectHorizontalDragGestures(
                        onDragEnd = { currentCommit(currentScale.fromFraction(fraction)) },
                        onDragCancel = { currentCommit(currentScale.fromFraction(fraction)) },
                    ) { change, dragX ->
                        change.consume()
                        fraction = (fraction - dragX / (size.width * RULER_SCREENS)).coerceIn(0f, 1f)
                        val tick = (fraction * size.width * RULER_SCREENS / tickPx).roundToInt().toFloat()
                        if (tick != lastTick) {
                            lastTick = tick
                            haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        }
                    }
                },
        ) {
            val tick = 10.dp.toPx()
            val total = size.width * RULER_SCREENS
            val center = size.width / 2f
            val offset = fraction * total
            val count = (total / tick).toInt()
            for (i in 0..count) {
                val x = center + i * tick - offset
                if (x < -tick || x > size.width + tick) continue
                val major = i % MAJOR_EVERY == 0
                val fade = (1f - abs(x - center) / center).coerceIn(0.15f, 1f)
                drawLine(
                    Palette.Paper.copy(alpha = 0.75f * fade),
                    Offset(x, size.height),
                    Offset(x, size.height - (if (major) 22.dp.toPx() else 12.dp.toPx())),
                    strokeWidth = 1.5.dp.toPx(),
                    cap = StrokeCap.Round,
                )
            }
            drawLine(Palette.Ochre, Offset(center, size.height), Offset(center, size.height - 30.dp.toPx()), 3.dp.toPx(), StrokeCap.Round)
        }
    }
}
