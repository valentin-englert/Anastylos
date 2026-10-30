package org.anastylos.capture.ui.camera

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.anastylos.capture.camera.UsableCamera

/**
 * Minimal camera selector shared by Live Observer and Guided
 * Capture: one chip per usable camera, nothing else. Hidden while
 * the inventory is empty (before first bind); a substitution note
 * renders verbatim below the chips, never silently.
 */
@Composable
fun CameraPicker(
    cameras: List<UsableCamera>,
    effectiveId: String?,
    note: String?,
    onSelect: (String) -> Unit,
) {
    if (cameras.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Camera", style = MaterialTheme.typography.titleSmall)
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
        ) {
            cameras.forEach { camera ->
                FilterChip(
                    selected = camera.id == effectiveId,
                    onClick = { onSelect(camera.id) },
                    label = { Text(camera.label) },
                )
            }
        }
        note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
    }
}
