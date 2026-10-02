"use client";

import { useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { EllipsisIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Spinner } from "@/components/ui/spinner";
import { refusal } from "@/lib/access";
import { ApiError, type Lecture } from "@/lib/api";
import { deleteLecture, forgetLecture } from "@/lib/lectures";
import { lectureKey } from "@/lib/queries";

import { useAccount } from "./account";
import { Callout } from "./common";

/** Why a lecture can't be deleted yet, if it can't: the API refuses while it's processing. */
export function deleteBlocked(lecture: Lecture): string | null {
  return lecture.status === "processing" ? "Delete it once processing is done." : null;
}

/** Asks before deleting a lecture, and stays open with the API's reason if it refuses.
 *  `onDeleted` runs before the caches forget it, so a page showing it can stop asking for it. */
export function DeleteLectureDialog({
  lecture,
  open,
  onOpenChange,
  onDeleted,
}: {
  lecture: Lecture;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const { quotas } = useAccount();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteLecture(lecture.id);
    } catch (e) {
      // Not found: deleted elsewhere already, which is what was asked.
      if (!(e instanceof ApiError && e.status === 404)) {
        setError(refusal(e));
        setBusy(false);
        // Processing started since the page loaded: show it.
        if (e instanceof ApiError && e.status === 409) {
          void queryClient.invalidateQueries({ queryKey: lectureKey(lecture.id), exact: true });
          void queryClient.invalidateQueries({ queryKey: ["lectures"] });
        }
        return;
      }
    }
    onDeleted?.();
    void forgetLecture(queryClient, lecture);
    toast.success(`Deleted “${lecture.title}”`);
    setBusy(false);
    onOpenChange(false);
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        if (!next) setError(null);
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete “{lecture.title}”?</AlertDialogTitle>
          <AlertDialogDescription>
            Its video, transcript, slides, notes and quiz go, with its processing history and the conversations
            about it. This can&apos;t be undone.
            {quotas && " It still counts towards today's uploads."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && <Callout tone="error">{error}</Callout>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            onClick={(event) => {
              // Stays open until the API answers, to show why if it refuses.
              event.preventDefault();
              void remove();
            }}
          >
            {busy && <Spinner />} Delete lecture
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** A lecture's own menu on a card or row in a list, for its owner: shows on hover, or always
 *  where there's no hover. A sibling of the card's link, so opening it doesn't open the lecture. */
export function LectureMenu({ lecture, className }: { lecture: Lecture; className?: string }) {
  const [confirm, setConfirm] = useState(false);
  const blocked = deleteBlocked(lecture);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="secondary"
            size="icon"
            aria-label={`More actions for ${lecture.title}`}
            className={cn(
              "size-7 shadow-sm transition-opacity data-[state=open]:opacity-100 pointer-fine:opacity-0 pointer-fine:group-hover/card:opacity-100 pointer-fine:focus-visible:opacity-100",
              className,
            )}
          >
            <EllipsisIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuItem variant="destructive" disabled={blocked !== null} onSelect={() => setConfirm(true)}>
            <Trash2Icon /> Delete lecture
          </DropdownMenuItem>
          {blocked && <p className="px-2 pb-1.5 text-xs text-muted-foreground">{blocked}</p>}
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteLectureDialog lecture={lecture} open={confirm} onOpenChange={setConfirm} />
    </>
  );
}
