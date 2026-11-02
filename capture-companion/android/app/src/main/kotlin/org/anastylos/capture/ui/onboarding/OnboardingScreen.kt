package org.anastylos.capture.ui.onboarding

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.clickable
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import org.anastylos.capture.R
import org.anastylos.capture.ui.theme.Glyph
import org.anastylos.capture.ui.theme.GlyphIcon
import org.anastylos.capture.ui.theme.Palette

private enum class Hero { Mark, Qr, Request, Wheel, Bolt, Muse }

private data class Page(val hero: Hero, val title: String, val body: String)

private val PAGES = listOf(
    Page(Hero.Mark, "Welcome to ANASTYLOS", "This phone is the camera at your workbench. It sends sharp, labelled photos to the workstation, so every removed part is remembered."),
    Page(Hero.Qr, "1 · Connect", HelpTopic.Connect.body),
    Page(Hero.Request, "2 · Take the photos it asks for", HelpTopic.Requests.body),
    Page(Hero.Wheel, "3 · Tune the picture", "Tap ISO, SPEED, EV, FOCUS or WB along the bottom of the camera. A wheel opens: swipe left or right and the values click into place. Tap the (i) above any wheel for an explanation."),
    Page(Hero.Bolt, "4 · Light and more settings", "The bolt switches the flashlight. The menu next to it holds the camera choice, profiles, zoom and picture sizes. You can replay this guide there."),
    Page(Hero.Muse, "5 · Talk to Muse", HelpTopic.Voice.body),
)

/**
 * First-run guide. [initialShowAtLaunch] is the stored preference; the
 * last page lets the user keep or drop the guide for the next launch.
 */
@Composable
fun OnboardingScreen(initialShowAtLaunch: Boolean, onFinish: (showAtLaunch: Boolean) -> Unit) {
    var index by remember { mutableIntStateOf(0) }
    var showAgain by remember { mutableStateOf(initialShowAtLaunch) }
    val page = PAGES[index]
    val last = index == PAGES.lastIndex
    Column(
        Modifier.fillMaxSize().background(Palette.Ground).statusBarsPadding().navigationBarsPadding().padding(24.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = { onFinish(showAgain) }) { Text("Skip", color = Palette.Muted) }
        }
        Column(Modifier.weight(1f).fillMaxWidth(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
            HeroArt(page.hero)
            Spacer(Modifier.height(36.dp))
            Text(page.title, style = MaterialTheme.typography.headlineMedium, color = Palette.Paper)
            Spacer(Modifier.height(14.dp))
            Text(page.body, style = MaterialTheme.typography.bodyLarge, color = Palette.Muted)
        }
        Dots(count = PAGES.size, current = index)
        Spacer(Modifier.height(20.dp))
        if (last) {
            Row(
                Modifier.fillMaxWidth().padding(bottom = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Show this guide when I open the app", color = Palette.Paper, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                Switch(
                    checked = showAgain,
                    onCheckedChange = { showAgain = it },
                    colors = SwitchDefaults.colors(checkedTrackColor = Palette.Ochre, checkedThumbColor = Palette.Ground),
                )
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (index > 0) {
                OutlinedButton(onClick = { index-- }, modifier = Modifier.weight(1f).height(56.dp)) { Text("Back", color = Palette.Paper) }
            }
            Button(
                onClick = { if (last) onFinish(showAgain) else index++ },
                colors = ButtonDefaults.buttonColors(containerColor = Palette.Ochre, contentColor = Palette.Ground),
                modifier = Modifier.weight(1f).height(56.dp),
            ) { Text(if (last) "Get started" else "Next", style = MaterialTheme.typography.titleMedium) }
        }
    }
}

@Composable
private fun HeroArt(hero: Hero) {
    Box(Modifier.size(120.dp).clip(CircleShape).background(Palette.Surface), contentAlignment = Alignment.Center) {
        when (hero) {
            Hero.Mark -> Image(painterResource(R.drawable.ic_launcher_foreground), null, Modifier.size(96.dp))
            Hero.Qr -> GlyphIcon(Glyph.Qr, Palette.Ochre, size = 56.dp)
            Hero.Request -> GlyphIcon(Glyph.Switch, Palette.Ochre, size = 56.dp)
            Hero.Wheel -> Text("ISO 400", color = Palette.Ochre, style = MaterialTheme.typography.titleLarge)
            Hero.Bolt -> GlyphIcon(Glyph.Bolt, Palette.Ochre, size = 56.dp)
            Hero.Muse -> Image(painterResource(R.drawable.muse_logo), null, Modifier.size(72.dp).clip(RoundedCornerShape(18.dp)))
        }
    }
}

@Composable
private fun Dots(count: Int, current: Int) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
        repeat(count) { i ->
            Box(
                Modifier
                    .padding(horizontal = 4.dp)
                    .size(if (i == current) 10.dp else 7.dp)
                    .clip(CircleShape)
                    .background(if (i == current) Palette.Ochre else Palette.Hairline),
            )
        }
    }
}

/** The (i) explanation dialog used next to every advanced control. */
@Composable
fun HelpDialog(topic: HelpTopic, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Palette.SurfaceRaised,
        title = { Text(topic.title, color = Palette.Paper) },
        text = { Text(topic.body, color = Palette.Paper.copy(alpha = 0.85f), style = MaterialTheme.typography.bodyLarge) },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Got it", color = Palette.Ochre) } },
    )
}
