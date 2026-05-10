# Privacy Policy — H1B Checker for LinkedIn Jobs

**Last updated:** May 9, 2026

## Overview

This policy describes how the **H1B Checker for LinkedIn Jobs** browser extension (“Extension”) handles information when you use it on LinkedIn job pages.

## Information the Extension Accesses

The Extension runs only on LinkedIn URLs under `https://www.linkedin.com/jobs/*`. It reads **company names** from job listing elements that are **already visible on your screen** in the page DOM. It does **not** access your LinkedIn account settings, messages, feed, profile data, or credentials.

## Information Sent to Our Servers

When you view a job listing, the Extension may send the **visible company name** (plain text) to our backend API over **HTTPS**:

- **Endpoint:** `https://h1bchecker-production.up.railway.app` (or as configured in the Extension)
- **Purpose:** To look up whether that employer appears in aggregated U.S. Department of Labor (DOL) H-1B LCA disclosure data and to return a yes/no style result for display on the page.

The Extension does **not** send your name, email, LinkedIn username, passwords, cookies, résumé, application history, or general browsing history.

## What We Do Not Do

- We do **not** sell your data to third parties.
- We do **not** use the Extension to build a cross-site tracking profile.
- We do **not** intentionally collect personally identifiable information (PII) through the Extension.

## Server-Side Processing and Logs

Our API and hosting provider (e.g., Railway) may create **standard server logs** (such as timestamps, IP addresses, and request paths) as part of normal HTTPS operations. Those logs are used for **security, reliability, and abuse prevention**, not for advertising. Retention depends on the hosting provider and our operational practices.

## Data Source

Sponsorship-related statistics shown by the Extension are derived from **public DOL LCA disclosure datasets** that we aggregate into our database. The Extension does not scrape LinkedIn at scale or store LinkedIn page HTML on our servers.

## Children

The Extension is not directed at children under 13, and we do not knowingly collect information from children.

## Changes

We may update this policy from time to time. The “Last updated” date at the top will change when we do.

## Contact

Questions or requests regarding this policy: open an issue on  
**https://github.com/ggggzj/H1B_Checker/issues**  
or contact the maintainer via the email listed on that GitHub profile.
