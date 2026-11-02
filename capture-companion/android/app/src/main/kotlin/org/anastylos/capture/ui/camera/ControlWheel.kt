package org.anastylos.capture.ui.camera

import androidx.compose.foundation.gestures.snapping.rememberSnapFlingBehavior
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import kotlin.math.abs
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.background
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.map
import org.anastylos.capture.ui.theme.Palette

private val STOP_WIDTH = 64.dp

/**
 * Horizontal setting wheel: swipe left or right and the values lock
 * into place under the ochre marker. [onSettled] fires only once the
 * wheel has come to rest on a detent.
 */
@Composable
fun <T> ControlWheel(
    stops: List<WheelStop<T>>,
    selectedIndex: Int,
    onSettled: (WheelStop<T>) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (stops.isEmpty()) return
    val state = rememberLazyListState(initialFirstVisibleItemIndex = selectedIndex.coerceIn(0, stops.lastIndex))
    val haptic = LocalHapticFeedback.current
    val centered by remember { derivedStateOf { state.centeredIndex(stops.lastIndex) } }
    LaunchedEffect(state, stops) {
        snapshotFlow { state.isScrollInProgress to state.centeredIndex(stops.lastIndex) }
            .filter { (scrolling, _) -> !scrolling }
            .map { (_, index) -> index }
            .distinctUntilChanged()
            .collect { index -> if (index != selectedIndex) onSettled(stops[index]) }
    }
    LaunchedEffect(centered) { haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove) }
    BoxWithConstraints(modifier.fillMaxWidth().height(76.dp)) {
        val side = (maxWidth - STOP_WIDTH) / 2
        LazyRow(
            state = state,
            flingBehavior = rememberSnapFlingBehavior(state),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = side),
            verticalAlignment = Alignment.Bottom,
            modifier = Modifier.fillMaxWidth(),
        ) {
            itemsIndexed(stops) { index, stop ->
                Column(Modifier.width(STOP_WIDTH), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        stop.label,
                        color = if (index == centered) Palette.Ochre else Palette.Paper.copy(alpha = 0.7f),
                        fontSize = if (index == centered) 22.sp else 16.sp,
                        fontWeight = if (index == centered) FontWeight.Bold else FontWeight.Normal,
                    )
                    Ticks(highlight = index == centered)
                }
            }
        }
    }
}

@Composable
private fun Ticks(highlight: Boolean) {
    Row(
        Modifier.padding(top = 8.dp).fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.Bottom,
    ) {
        repeat(4) { tick ->
            val major = tick == 0
            Box(
                Modifier
                    .size(width = if (major && highlight) 3.dp else 1.5.dp, height = if (major) 22.dp else 12.dp)
                    .background(if (major && highlight) Palette.Ochre else Palette.Paper.copy(alpha = 0.55f)),
            )
        }
    }
}

/** The item whose middle is closest to the middle of the viewport. */
private fun LazyListState.centeredIndex(lastIndex: Int): Int {
    val info = layoutInfo
    val viewportCenter = (info.viewportStartOffset + info.viewportEndOffset) / 2
    val closest = info.visibleItemsInfo.minByOrNull { abs(it.offset + it.size / 2 - viewportCenter) }
    return (closest?.index ?: 0).coerceIn(0, lastIndex)
}
