import { motion } from "framer-motion";
import type { Book } from "../api/types";

/** Generated cloth-bound cover: muted hue from the book, gold frame, title set in the display face. */
export function BookCover({ book, size = "md" }: { book: Pick<Book, "title" | "author" | "cover_hue">; size?: "sm" | "md" | "lg" }) {
  const h = book.cover_hue;
  const dims = { sm: "w-24 h-36 text-sm", md: "w-full aspect-[2/3] text-lg", lg: "w-48 sm:w-56 aspect-[2/3] text-2xl" }[size];
  return (
    <div
      className={`relative ${dims} overflow-hidden rounded-r-md rounded-l-sm`}
      style={{
        background: `linear-gradient(160deg, hsl(${h} 28% 30%), hsl(${h} 32% 18%))`,
        boxShadow: "inset 6px 0 10px -4px #0008, inset -1px 0 0 #fff1, 0 12px 28px -12px #000a",
      }}
    >
      {/* spine crease */}
      <div className="absolute inset-y-0 left-2 w-px bg-black/30" />
      {/* frame */}
      <div className="absolute inset-3 rounded-sm border" style={{ borderColor: "#e3b86666" }} />
      <div className="absolute inset-[18px] rounded-sm border" style={{ borderColor: "#e3b86633" }} />
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center">
        <Emblem hue={h} />
        <h3 className="font-display font-semibold leading-tight" style={{ color: "#efd9a6" }}>
          {book.title}
        </h3>
        {book.author && (
          <p className="font-display text-[0.7em] italic" style={{ color: "#e9dcc0b3" }}>
            {book.author}
          </p>
        )}
      </div>
      {/* sheen */}
      <div className="absolute inset-0 bg-gradient-to-tr from-transparent via-white/5 to-transparent" />
    </div>
  );
}

function Emblem({ hue }: { hue: number }) {
  // Pick one of a few ornaments by hue so each book feels distinct.
  const kind = Math.floor(hue / 72) % 5;
  const stroke = "#e3b866";
  return (
    <svg width="34" height="34" viewBox="0 0 40 40" className="mb-1 opacity-80" aria-hidden>
      {kind === 0 && <path d="M20 4 L23 17 L36 20 L23 23 L20 36 L17 23 L4 20 L17 17 Z" fill="none" stroke={stroke} strokeWidth="1.2" />}
      {kind === 1 && <path d="M26 6 A14 14 0 1 0 26 34 A11 11 0 1 1 26 6 Z" fill="none" stroke={stroke} strokeWidth="1.2" />}
      {kind === 2 && (
        <g fill="none" stroke={stroke} strokeWidth="1.2">
          <circle cx="20" cy="20" r="12" />
          <path d="M20 8 V32 M8 20 H32" />
        </g>
      )}
      {kind === 3 && <path d="M20 5 L33 20 L20 35 L7 20 Z M20 12 L27 20 L20 28 L13 20 Z" fill="none" stroke={stroke} strokeWidth="1.2" />}
      {kind === 4 && (
        <g fill="none" stroke={stroke} strokeWidth="1.2">
          <path d="M8 30 Q20 4 32 30" />
          <path d="M14 30 Q20 16 26 30" />
        </g>
      )}
    </svg>
  );
}

/** Cover that lifts and tilts on hover. */
export function TiltCover({ book }: { book: Book }) {
  return (
    <motion.div
      whileHover={{ y: -6, rotateY: -8, rotateX: 3 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
      style={{ transformPerspective: 900 }}
    >
      <BookCover book={book} />
    </motion.div>
  );
}
