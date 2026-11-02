package org.anastylos.capture.ui.theme

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Hand-drawn line icons on a 24-unit grid; no icon library dependency. */
enum class Glyph { Menu, Bolt, Qr, Info, Close, Back, Switch }

@Composable
fun GlyphIcon(glyph: Glyph, tint: Color, modifier: Modifier = Modifier, size: Dp = 24.dp, filled: Boolean = false) {
    Canvas(modifier.size(size)) {
        val u = this.size.minDimension / 24f
        val stroke = Stroke(width = 1.8f * u, cap = StrokeCap.Round)
        fun p(x: Float, y: Float) = Offset(x * u, y * u)
        when (glyph) {
            Glyph.Menu -> listOf(6f, 12f, 18f).forEach { y -> drawLine(tint, p(3f, y), p(21f, y), 1.8f * u, StrokeCap.Round) }
            Glyph.Close -> {
                drawLine(tint, p(5f, 5f), p(19f, 19f), 1.8f * u, StrokeCap.Round)
                drawLine(tint, p(19f, 5f), p(5f, 19f), 1.8f * u, StrokeCap.Round)
            }
            Glyph.Back -> {
                drawLine(tint, p(15f, 4f), p(7f, 12f), 1.8f * u, StrokeCap.Round)
                drawLine(tint, p(7f, 12f), p(15f, 20f), 1.8f * u, StrokeCap.Round)
            }
            Glyph.Bolt -> {
                val bolt = Path().apply {
                    moveTo(13.5f * u, 2f * u); lineTo(5f * u, 13.5f * u); lineTo(11f * u, 13.5f * u)
                    lineTo(10.2f * u, 22f * u); lineTo(19f * u, 10f * u); lineTo(13f * u, 10f * u); close()
                }
                if (filled) drawPath(bolt, tint) else drawPath(bolt, tint, style = stroke)
            }
            Glyph.Qr -> {
                listOf(Offset(3f, 3f), Offset(14f, 3f), Offset(3f, 14f)).forEach { o ->
                    drawRect(tint, p(o.x, o.y), Size(7f * u, 7f * u), style = stroke)
                    drawRect(tint, p(o.x + 2.5f, o.y + 2.5f), Size(2f * u, 2f * u))
                }
                drawRect(tint, p(14f, 14f), Size(3f * u, 3f * u))
                drawRect(tint, p(18.5f, 18.5f), Size(2.5f * u, 2.5f * u))
                drawRect(tint, p(18.5f, 14f), Size(2.5f * u, 2.5f * u))
                drawRect(tint, p(14f, 18.5f), Size(2.5f * u, 2.5f * u))
            }
            Glyph.Info -> {
                drawCircle(tint, 9.5f * u, p(12f, 12f), style = stroke)
                drawCircle(tint, 1.1f * u, p(12f, 7.6f))
                drawLine(tint, p(12f, 11f), p(12f, 17f), 1.8f * u, StrokeCap.Round)
            }
            Glyph.Switch -> {
                val a = Path().apply { moveTo(4f * u, 9f * u); lineTo(19f * u, 9f * u); lineTo(15.5f * u, 5.5f * u) }
                val b = Path().apply { moveTo(20f * u, 15f * u); lineTo(5f * u, 15f * u); lineTo(8.5f * u, 18.5f * u) }
                drawPath(a, tint, style = stroke); drawPath(b, tint, style = stroke)
            }
        }
    }
}
