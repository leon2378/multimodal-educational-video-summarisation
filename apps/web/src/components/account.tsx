"use client";

import { UserButton, useAuth, useClerk } from "@clerk/nextjs";
import { useQueryClient } from "@tanstack/react-query";
import { GaugeIcon, LockIcon, LogInIcon } from "lucide-react";
import { type ReactNode, createContext, use, useEffect, useRef } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { allowance, canChange, resetText } from "@/lib/access";
import { ApiError, type Visibility } from "@/lib/api";
import { CLERK_KEY, setTokenSource } from "@/lib/auth";
import { formatBytes, pluralise } from "@/lib/format";
import { useMe } from "@/lib/queries";

import { Callout } from "./common";

interface ClerkState {
  /** The web app was built with a Clerk key, so it can sign in. */
  enabled: boolean;
  signedIn: boolean;
  signIn: () => void;
  signUp: () => void;
  signOut: () => void;
}

const NO_CLERK: ClerkState = { enabled: false, signedIn: false, signIn() {}, signUp() {}, signOut() {} };
const ClerkContext = createContext<ClerkState>(NO_CLERK);

/** Signing in, when the web app has a Clerk key; without one, nothing: the API then decides
 *  (with sign-in off there, everyone is its one local user). */
export function AuthProvider({ children }: { children: ReactNode }) {
  return CLERK_KEY ? <ClerkBridge>{children}</ClerkBridge> : <ClerkContext value={NO_CLERK}>{children}</ClerkContext>;
}

/** Hands Clerk's session token to API calls, and refetches everything when the user changes,
 *  since what the API returns depends on who asks. Needs the ClerkProvider in layout.tsx. */
function ClerkBridge({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, userId, getToken } = useAuth();
  const clerk = useClerk();
  const queryClient = useQueryClient();
  const previous = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (isLoaded) setTokenSource(() => getToken());
  }, [isLoaded, getToken]);

  useEffect(() => {
    if (!isLoaded) return;
    const current = userId ?? null;
    if (previous.current !== undefined && previous.current !== current) void queryClient.resetQueries();
    previous.current = current;
  }, [isLoaded, userId, queryClient]);

  const state: ClerkState = {
    enabled: true,
    signedIn: Boolean(isSignedIn),
    signIn: () => clerk.openSignIn(),
    signUp: () => clerk.openSignUp(),
    signOut: () => void clerk.signOut(),
  };
  return <ClerkContext value={state}>{children}</ClerkContext>;
}

/** What the viewer may do. Until /v1/me answers, nothing gated is shown; if it fails (the API
 *  is down), things act as before sign-in existed and the calls themselves report the error. */
export function useAccount() {
  const clerk = use(ClerkContext);
  const me = useMe();
  const data = me.data;
  const unknown = data === undefined && me.isError;
  return {
    pending: me.isPending,
    /** Sign-in is on at the API. Off, the web app behaves as it always did. */
    auth: data?.auth ?? false,
    signedIn: data?.signed_in ?? unknown,
    admin: data?.admin ?? unknown,
    user: data?.user ?? null,
    quotas: data?.quotas ?? null,
    clerk,
    canChange: (item: { owner_id: string | null }) => (data ? canChange(data, item) : unknown),
  };
}

/** In place of something that needs an account. */
export function SignInPrompt({ title, children }: { title: string; children?: ReactNode }) {
  const { clerk } = useAccount();
  return (
    <div className="flex flex-col items-center justify-center gap-4 px-6 py-10 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-brand-soft text-brand-ink">
        <LogInIcon className="size-5" />
      </span>
      <div className="flex max-w-xs flex-col gap-1">
        <p className="font-semibold">{title}</p>
        {children && <div className="text-sm text-balance text-muted-foreground">{children}</div>}
      </div>
      {clerk.enabled ? (
        <div className="flex gap-2">
          <Button onClick={clerk.signIn}>Sign in</Button>
          <Button variant="outline" onClick={clerk.signUp}>
            Create an account
          </Button>
        </div>
      ) : (
        <p className="max-w-xs text-xs text-muted-foreground">
          This web app was built without a Clerk key, so it can&apos;t sign in.
        </p>
      )}
    </div>
  );
}

/** The header's end: sign in, or what's left today and the account menu. Nothing with sign-in
 *  off at the API. */
export function AccountMenu() {
  const { auth, quotas, clerk } = useAccount();
  if (!auth || !clerk.enabled) return null;
  if (!clerk.signedIn) {
    return (
      <Button variant="outline" onClick={clerk.signIn}>
        <LogInIcon />
        <span className="hidden sm:inline">Sign in</span>
      </Button>
    );
  }
  return (
    <>
      {quotas && <UsageChip />}
      <span className="flex size-9 items-center justify-center">
        <UserButton />
      </span>
    </>
  );
}

function UsageChip() {
  const { quotas } = useAccount();
  if (!quotas) return null;
  const left = allowance(quotas);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="hidden h-9 cursor-default items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground tabular-nums lg:flex">
          <GaugeIcon className="size-4" />
          {left.questions} · {left.uploads}
        </span>
      </TooltipTrigger>
      <TooltipContent className="flex flex-col gap-0.5">
        <span>
          {pluralise(left.questions, "question")} of {quotas.questions_per_day} left today
        </span>
        <span>
          {pluralise(left.uploads, "upload")} of {quotas.uploads_per_day} left today, up to{" "}
          {formatBytes(quotas.upload_bytes)} each
        </span>
        <span>More at {resetText(quotas.resets_at)}</span>
      </TooltipContent>
    </Tooltip>
  );
}

/** Says when signing in can't work, and when everyone's daily budget is spent. */
export function AuthBanner() {
  const { auth, quotas, clerk } = useAccount();
  const me = useMe();
  const rejected = me.error instanceof ApiError && me.error.status === 401 && clerk.signedIn;

  let banner: ReactNode = null;
  if (rejected) {
    banner = (
      <Callout
        tone="warning"
        title="The API didn't accept your sign-in"
        action={
          <Button variant="outline" size="sm" onClick={clerk.signOut}>
            Sign out
          </Button>
        }
      >
        {me.error?.message} Its AUTH_ISSUER must be this Clerk instance, and its authorised parties this site.
      </Callout>
    );
  } else if (auth && !clerk.enabled) {
    banner = (
      <Callout tone="warning" title="Sign-in is on at the API, but this web app can't sign in">
        It was built without NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY. You can read and search the public lectures.
      </Callout>
    );
  } else if (quotas?.paused) {
    banner = (
      <Callout tone="info" title="Today's shared budget for the language model is spent">
        Questions and processing resume at {resetText(quotas.resets_at)}. Reading and search still work.
      </Callout>
    );
  }
  return banner && <div className="mx-auto w-full max-w-[1600px] px-4 pt-4 sm:px-6">{banner}</div>;
}

/** Marks a private lecture or course: its owner's, or someone else's that an admin sees. */
export function PrivateBadge({ visibility, className }: { visibility: Visibility; className?: string }) {
  if (visibility !== "private") return null;
  return (
    <Badge variant="secondary" className={className}>
      <LockIcon /> Private
    </Badge>
  );
}
