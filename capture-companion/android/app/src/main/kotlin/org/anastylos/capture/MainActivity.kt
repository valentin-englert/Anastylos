package org.anastylos.capture

import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import org.anastylos.capture.di.AppContainer
import org.anastylos.capture.di.CaptureApp
import org.anastylos.capture.ui.pair.PairViewModel
import org.anastylos.capture.ui.pair.QrPairRoute
import org.anastylos.capture.ui.session.SessionScreen
import org.anastylos.capture.ui.guided.GuidedCaptureScreen
import org.anastylos.capture.ui.session.SessionViewModel

class MainActivity : ComponentActivity() {
    private val container: AppContainer get() = (application as CaptureApp).container

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AnastylosApp(container)
        }
    }

    // Volume-down is the push-to-talk fallback, only while voice mode is on.
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN && container.voice.state.value.listening) {
            if (event.repeatCount == 0) container.voice.pushToTalkDown()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN && container.voice.state.value.listening) {
            container.voice.pushToTalkUp()
            return true
        }
        return super.onKeyUp(keyCode, event)
    }
}

@Composable
fun AnastylosApp(container: AppContainer) {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            val navController = rememberNavController()
            NavHost(navController = navController, startDestination = "session") {
                composable("session") {
                    val sessionViewModel: SessionViewModel = viewModel(
                        factory = container.sessionViewModelFactory(),
                    )
                    SessionScreen(
                        sessionViewModel,
                        onConnect = { navController.navigate("pair") },
                        onGuided = { navController.navigate("guided") },
                    )
                }
                composable("guided") {
                    GuidedCaptureScreen(container, onBack = { navController.popBackStack() })
                }
                composable("pair") {
                    val pairViewModel: PairViewModel = viewModel(
                        factory = container.pairViewModelFactory(),
                    )
                    QrPairRoute(pairViewModel, onDone = { navController.popBackStack() })
                }
            }
        }
    }
}
