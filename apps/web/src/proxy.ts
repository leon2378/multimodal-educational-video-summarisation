import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

/** Clerk's middleware keeps its session in step across tabs and refreshes. The API checks
 *  every token itself, so no page is protected here. Without both Clerk keys, as in local
 *  development and CI, requests pass straight through. */
const clerk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY;

export default clerk ? clerkMiddleware() : () => NextResponse.next();

export const config = {
  matcher: [
    // Everything except Next's own files and static assets, as Clerk recommends.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
  ],
};
