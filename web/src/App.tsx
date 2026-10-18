import { AnimatePresence } from "framer-motion";
import { Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./auth/AuthContext";
import { Ambient } from "./components/Ambient";
import { Navbar } from "./components/Navbar";
import { IngestionProvider } from "./lib/ingestion";
import { ToastProvider } from "./lib/ui";
import Landing from "./pages/Landing";
import AuthPage from "./pages/AuthPage";
import Library from "./pages/Library";
import BookPage from "./pages/BookPage";
import Upload from "./pages/Upload";
import Workspace from "./pages/Workspace";
import Explore from "./pages/Explore";
import Shelf from "./pages/Shelf";
import NotFound from "./pages/NotFound";

export default function App() {
  const loc = useLocation();
  // The workspace keeps one key per book so switching storylines doesn't replay the page transition.
  const key = loc.pathname.startsWith("/read/") ? loc.pathname.split("/").slice(0, 3).join("/") : loc.pathname;
  return (
    <AuthProvider>
      <ToastProvider>
        <IngestionProvider>
        <Ambient />
        <div className="flex min-h-full flex-col">
          <Navbar />
          <AnimatePresence mode="wait">
            <Routes location={loc} key={key}>
              <Route path="/" element={<Landing />} />
              <Route path="/signin" element={<AuthPage mode="signin" />} />
              <Route path="/signup" element={<AuthPage mode="signup" />} />
              <Route path="/library" element={<RequireAuth><Library /></RequireAuth>} />
              <Route path="/books/:bookId" element={<RequireAuth><BookPage /></RequireAuth>} />
              <Route path="/explore" element={<RequireAuth><Explore /></RequireAuth>} />
              <Route path="/upload" element={<RequireAuth><Upload /></RequireAuth>} />
              <Route path="/shelf" element={<RequireAuth><Shelf /></RequireAuth>} />
              <Route path="/read/:bookId/:storylineId?" element={<RequireAuth><Workspace /></RequireAuth>} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </AnimatePresence>
        </div>
        </IngestionProvider>
      </ToastProvider>
    </AuthProvider>
  );
}
