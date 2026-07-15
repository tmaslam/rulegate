"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getSession } from "@/lib/auth";
import { RejectionHero } from "./RejectionHero";
import { SignInForm } from "./SignInForm";
import styles from "./login.module.css";

/**
 * Login layout: the hero and the form, side by side.
 *
 * The hero is on the left because it is the argument and the form is the door.
 * On narrow screens the form comes FIRST — a visitor on a phone should not have
 * to scroll past the pitch to get in.
 */
export function LoginClient() {
  const router = useRouter();

  // Already signed in? Don't make them do it twice.
  useEffect(() => {
    if (getSession()) router.replace("/queue");
  }, [router]);

  return (
    <>
      <a href="#signin" className="skip">
        Skip to sign in
      </a>
      <main className={styles.split}>
        <section className={styles.heroSide} aria-label="How the policy engine handles an unsafe request">
          <RejectionHero />
        </section>
        <section className={styles.formSide} id="signin">
          <SignInForm />
        </section>
      </main>
    </>
  );
}
