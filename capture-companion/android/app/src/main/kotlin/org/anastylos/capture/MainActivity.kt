package org.anastylos.capture

import android.os.Bundle
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
import org.anastylos.capture.ui.session.SessionViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as CaptureApp).container
        setContent {
            AnastylosApp(container)
        }
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
                    SessionScreen(sessionViewModel, onConnect = { navController.navigate("pair") })
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
