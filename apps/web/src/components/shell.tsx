"use client";

import { PlusIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { API_URL } from "@/lib/api";
import { useHealth } from "@/lib/queries";

import { AccountMenu, AuthBanner } from "./account";
import { Callout, Logo } from "./common";
import { usePalette } from "./palette";
import { ThemeToggle } from "./theme";
import { useUpload } from "./upload";

export function AppShell({ children }: { children: ReactNode }) {
  const { openUpload } = useUpload();
  const { openPalette } = usePalette();
  // Ctrl until mounted, so the server's HTML matches the first render.
  const [mod, setMod] = useState("Ctrl");
  useEffect(() => {
    if (/Mac|iPhone|iPad/.test(navigator.userAgent)) setMod("⌘");
  }, []);
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur-lg">
        <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-3 px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5 rounded-md font-semibold tracking-tight">
            <Logo />
            <span className="hidden sm:inline">Lecture Summariser</span>
          </Link>
          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            <button
              type="button"
              onClick={openPalette}
              className="flex h-9 items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm text-muted-foreground transition-colors hover:bg-muted sm:w-64 lg:w-80"
            >
              <SearchIcon className="size-4" />
              <span className="hidden flex-1 text-left sm:inline">Search lectures…</span>
              <Kbd className="hidden sm:inline-flex">{mod} K</Kbd>
            </button>
            <ApiStatus />
            <ThemeToggle />
            <Button onClick={() => openUpload()}>
              <PlusIcon />
              <span className="hidden sm:inline">Add lecture</span>
            </Button>
            <AccountMenu />
          </div>
        </div>
      </header>
      <ApiBanner />
      <AuthBanner />
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-6 sm:px-6 lg:py-8">{children}</main>
    </div>
  );
}

function ApiStatus() {
  const health = useHealth();
  const up = health.data?.ok === true;
  const label = health.isPending
    ? "Checking the API…"
    : up
      ? "API, database and storage are up"
      : health.isError
        ? "Can't reach the API"
        : "The API is up, but not everything behind it is";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="hidden size-9 items-center justify-center md:flex" role="status" aria-label={label}>
          <span className="relative flex size-2.5">
            {!up && !health.isPending && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-destructive/60" />
            )}
            <span
              className={`relative inline-flex size-2.5 rounded-full ${
                health.isPending ? "bg-muted-foreground/40" : up ? "bg-success" : "bg-destructive"
              }`}
            />
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** Says what's wrong when the API is down, instead of every panel failing on its own. */
function ApiBanner() {
  const health = useHealth();
  if (health.isPending || health.data?.ok) return null;
  const broken = Object.entries(health.data?.checks ?? {})
    .filter(([, result]) => result !== "ok")
    .map(([name]) => name);
  return (
    <div className="mx-auto w-full max-w-[1600px] px-4 pt-4 sm:px-6">
      <Callout
        tone="warning"
        title={health.isError ? `Can't reach the API at ${API_URL}` : `The API can't reach its ${broken.join(" or ")}`}
        action={
          <Button variant="outline" size="sm" onClick={() => void health.refetch()}>
            Retry
          </Button>
        }
      >
        {health.isError ? (
          <>
            Start the stack with <code className="font-mono text-foreground">make up</code> and{" "}
            <code className="font-mono text-foreground">make app</code>. This checks again every few seconds.
          </>
        ) : (
          "Check that Postgres and SeaweedFS are running (make up)."
        )}
      </Callout>
    </div>
  );
}
