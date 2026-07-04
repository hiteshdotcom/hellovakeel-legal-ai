import { AnimatePresence, motion } from "framer-motion";
import { useUI } from "@/store/ui";
import TopBar from "./TopBar";
import Sidebar from "./Sidebar";
import ChatView from "@/components/chat/ChatView";
import SourcesDrawer from "@/components/sources/SourcesDrawer";
import JudgmentReader from "@/components/judgment/JudgmentReader";

export default function AppShell() {
  const { mobileNavOpen, setMobileNav } = useUI();

  return (
    <div className="flex h-[100dvh] w-full flex-col overflow-hidden bg-canvas">
      <TopBar />
      <div className="relative flex min-h-0 w-full flex-1">
        <Sidebar />
        {/* mobile scrim behind the drawer */}
        <AnimatePresence>
          {mobileNavOpen && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileNav(false)}
              className="fixed inset-x-0 bottom-0 top-12 z-20 bg-black/30 md:hidden"
              aria-hidden
            />
          )}
        </AnimatePresence>
        <ChatView />
      </div>
      <SourcesDrawer />
      <JudgmentReader />
    </div>
  );
}
