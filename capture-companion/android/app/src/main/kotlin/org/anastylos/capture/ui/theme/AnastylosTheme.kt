package org.anastylos.capture.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** Editorial dark palette: near-black ground, warm paper text, one ochre accent. */
object Palette {
    val Ground = Color(0xFF0B0B0C)
    val Surface = Color(0xFF151516)
    val SurfaceRaised = Color(0xFF1E1E20)
    val Hairline = Color(0xFF2E2E31)
    val Paper = Color(0xFFF2EFE8)
    val Muted = Color(0xFF9A978F)
    val Ochre = Color(0xFFE0B13A)
    val OchreDeep = Color(0xFF8C6D1F)
    val Sage = Color(0xFF8FB08F)
    val Brick = Color(0xFFD0705F)
    val Muse = Color(0xFF3D8DF5)

    /** Scrim behind overlay text on live camera: dark, not milky. */
    val Scrim = Color(0xA6000000)
}

private val scheme = darkColorScheme(
    primary = Palette.Ochre,
    onPrimary = Palette.Ground,
    secondary = Palette.Paper,
    onSecondary = Palette.Ground,
    background = Palette.Ground,
    onBackground = Palette.Paper,
    surface = Palette.Surface,
    onSurface = Palette.Paper,
    surfaceVariant = Palette.SurfaceRaised,
    onSurfaceVariant = Palette.Muted,
    outline = Palette.Hairline,
    error = Palette.Brick,
    onError = Palette.Ground,
)

private val headline = FontFamily.Serif

private val typography = Typography(
    headlineLarge = TextStyle(fontFamily = headline, fontWeight = FontWeight.Medium, fontSize = 34.sp, lineHeight = 38.sp, letterSpacing = (-0.5).sp),
    headlineMedium = TextStyle(fontFamily = headline, fontWeight = FontWeight.Medium, fontSize = 28.sp, lineHeight = 32.sp, letterSpacing = (-0.3).sp),
    headlineSmall = TextStyle(fontFamily = headline, fontWeight = FontWeight.Medium, fontSize = 22.sp, lineHeight = 28.sp),
    titleLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 20.sp, lineHeight = 26.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 17.sp, lineHeight = 22.sp),
    titleSmall = TextStyle(fontWeight = FontWeight.Medium, fontSize = 14.sp, letterSpacing = 0.6.sp),
    bodyLarge = TextStyle(fontSize = 17.sp, lineHeight = 25.sp),
    bodyMedium = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodySmall = TextStyle(fontSize = 13.sp, lineHeight = 18.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.Medium, fontSize = 15.sp, letterSpacing = 0.4.sp),
)

/** The app is dark by design; it never follows the system light theme. */
@Composable
fun AnastylosTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, typography = typography, content = content)
}
