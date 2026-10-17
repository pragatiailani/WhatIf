import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Page } from "../components/ui";

export default function NotFound() {
  return (
    <Page className="flex flex-col items-center py-24 text-center">
      <motion.p className="font-display text-8xl text-gold" animate={{ rotate: [0, -4, 4, 0] }} transition={{ duration: 5, repeat: Infinity }}>
        ?
      </motion.p>
      <h1 className="mt-4 font-display text-4xl">This page was never written</h1>
      <p className="mt-2 text-ink-soft">Perhaps it exists in another storyline.</p>
      <Link to="/library" className="mt-8 rounded-xl bg-gold px-5 py-2.5 font-medium text-[#1b1508]">Back to the library</Link>
    </Page>
  );
}
