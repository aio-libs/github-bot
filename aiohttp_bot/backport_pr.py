import asyncio
import os
import random

import gidgethub.routing
from kombu import exceptions as kombu_ex
from redis import exceptions as redis_ex

from . import tasks, util

router = gidgethub.routing.Router()


@router.register("pull_request", action="closed")
@router.register("pull_request", action="labeled")
async def backport_pr(event, gh, *args, **kwargs):
    if event.data["pull_request"]["merged"]:
        issue_number = event.data["pull_request"]["number"]
        merged_by = event.data["pull_request"]["merged_by"]["login"]
        created_by = event.data["pull_request"]["user"]["login"]

        commit_hash = event.data["pull_request"]["merge_commit_sha"]

        pr_labels = []
        if event.data["action"] == "labeled":
            pr_labels = [event.data["label"]]
        else:
            gh_issue = await util.issue_for_PR(gh, event.data["pull_request"])
            pr_labels = await gh.getitem(gh_issue["labels_url"])

            await util.remove_stage_labels(gh, gh_issue)

        print(pr_labels)
        branches = [
            label["name"].split()[-1]
            for label in pr_labels
            if label["name"].startswith("needs backport to")
        ]

        if branches:

            thanks_to = ""
            if created_by == merged_by or merged_by == "aiohttp-bot":
                thanks_to = f"Thanks @{created_by} for the PR 🌮🎉."
            else:
                thanks_to = f"Thanks @{created_by} for the PR, and @{merged_by} for merging it 🌮🎉."
            message = (
                f"{thanks_to}. I'm working now to backport this PR to: {', '.join(branches)}."
                f"\n🐍🍒⛏🤖"
            )

            await util.comment_on_pr(gh, issue_number, message)

            sorted_branches = sorted(
                branches, reverse=True, key=lambda v: tuple(map(int, v.split(".")))
            )

            for branch in sorted_branches:
                await kickoff_backport_task(
                    gh, commit_hash, branch, issue_number, created_by, merged_by
                )


async def kickoff_backport_task(
    gh, commit_hash, branch, issue_number, created_by, merged_by
):
    try:
        tasks.backport_task.delay(
            commit_hash,
            branch,
            issue_number=issue_number,
            created_by=created_by,
            merged_by=merged_by,
        )
    except (redis_ex.ConnectionError, kombu_ex.OperationalError) as ex:
        err_message = f"I'm having trouble backporting to `{branch}`. Reason: '`{ex}`'. Please retry by removing and re-adding the `needs backport to {branch}` label."
        await util.comment_on_pr(gh, issue_number, err_message)
