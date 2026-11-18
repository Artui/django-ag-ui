# User skills

A [`SkillRegistry`](api.md#skills) holds the skills your code ships. **User skills** are
the ones people write for themselves or for their team: rows of a model you own, read
and written through django-spec-agents' skills runtime (`django_spec_agents.skills`).
Hand `AGUIServer` the model's policy and the mount does three things with it:

- the `skills/` catalog lists each caller's stored skills beside the registry's;
- every run gets the caller's stored skills as deferred capabilities, and optionally
  the tools to author them;
- with `skill_editor=True`, a `skills/editor/` endpoint lists, creates, updates and
  turns off or on the skills the caller may write.

## The model and its policy

The runtime ships an abstract model and a protocol; the tenant relation and the rules
are yours. A `SkillPolicy` answers three questions: which skills a request sees
(`visible`), whether it may save one (`can_write`), and where a new one goes
(`new_skill`).

```python
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django_spec_agents.skills import AbstractSkill


class Skill(AbstractSkill):
    organisation = models.ForeignKey("orgs.Organisation", on_delete=models.CASCADE)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE
    )

    @classmethod
    def skill_policy(cls):
        return SkillPolicy()


class SkillPolicy:
    def visible(self, request):
        org = request.user.membership.organisation
        mine = Q(owner=request.user) | Q(owner__isnull=True)
        # A personal skill first, so it shadows the company one of the same name.
        return Skill.objects.filter(mine, organisation=org).order_by(
            F("owner").asc(nulls_last=True), "pk"
        )

    def can_write(self, request, skill):
        membership = request.user.membership
        if skill.organisation_id != membership.organisation_id:
            return False
        if skill.owner_id is None:
            return membership.is_admin
        return skill.owner_id == request.user.pk

    def new_skill(self, request):
        org = request.user.membership.organisation
        return Skill(organisation=org, owner=request.user)
```

Then mount it:

```python
agent = AGUIServer(
    registry,
    skills=built_in_skills,
    skill_policy=Skill.skill_policy(),
    skill_editor=True,
    skill_authoring=True,
)
```

The policy reads `request.user`. A `get_user=` hook on the mount sets it before any
skills view runs, so a token-authenticated mount works the same way.

**The registry's names are reserved.** The mount wraps your policy so that a stored
skill can never take a name a `SkillRegistry` skill holds: the editor and the authoring
tools refuse it, through the runtime's `ReservedSkillNames` check. A policy that defines
its own `reserved_names` keeps them; the mount adds its names to yours.

## The catalog

`GET skills/` returns the registry's entries, then the caller's stored skills: the
enabled ones `list_visible_skills` resolves (one per name, the first `visible` yields),
that are user-invocable. A stored entry is

```json
{"name": "triage", "title": "triage", "description": "Sort the inbox."}
```

and never carries a `prompt`. Picking it sends `/triage`. A registry skill wins a name
both hold. In this release a run reads a stored skill's body only when the skill is
also model-invocable, since django-spec-agents' `StoredSkills` offers only those: a skill
that is user-invocable but not model-invocable is listed, and a pick of it reaches the
model as the bare `/name`.

## The editor

`skill_editor=True` mounts `skills/editor/` and `skills/editor/<id>/`. They are the one
place a skill's body reaches the browser, and only for a skill the caller may write.

| Request | Answer |
| --- | --- |
| `GET skills/editor/` | `{"skills": [skill, ...]}`, every skill the caller sees and `can_write` admits, disabled ones included |
| `POST skills/editor/` with a whole skill | `201` and the created skill |
| `PUT skills/editor/<id>/` with a whole skill | `200` and the replaced skill (a rename is allowed) |
| `PATCH skills/editor/<id>/` with `{"enabled": false}` or `{"enabled": true}` | `200` and the skill, turned off or back on |

A skill on the wire:

```json
{
  "id": "42",
  "name": "triage",
  "description": "Sort the inbox.",
  "body": "Read every unread message, then ...",
  "userInvocable": true,
  "modelInvocable": true,
  "enabled": true
}
```

A write sends `name`, `description` and `body`, with `userInvocable` and
`modelInvocable` optional (both default to `true`). `id` is the row's primary key as a
string.

Every write goes through the runtime's `create_skill`, `update_skill`, `disable_skill`
and `enable_skill`, so the rules are the authoring tools' own:

| Status | When |
| --- | --- |
| `400` `{"error": "..."}` | the body is not a JSON object, a field has the wrong type, or a runtime rule breaks: the name is not kebab-case or is reserved, a description or body is empty or too long |
| `403` `{"error": "..."}` | the policy's `can_write` refuses the write |
| `404` `{"error": "not found"}` | the `<id>` is one the caller cannot see, or sees and may not write |
| `409` `{"error": "..."}` | the caller already sees the most enabled skills the runtime allows |
| `415` `{"error": "..."}` | a write whose content type is not `application/json` |

No error carries a skill's body. The `error` text names the broken rule but not the
field, so show it beside the form rather than under one field.

Like the thread and attachment endpoints, the editor is CSRF-exempt unless the mount
passes `csrf_exempt=False`. Its writes must be `application/json`, which a cross-site
form or `text/plain` post cannot send without a CORS preflight, so they are refused with
`415` otherwise.

## In a run

Every run of the mount gets `StoredSkills(policy, request)`: each enabled,
model-invocable skill the caller sees becomes a deferred capability, listed to the model
by name and description, its body loaded only when the model asks for it. A skill grants
no tools and no permissions; the tool guard and your authorization apply whatever its
body says.

`skill_authoring=True` adds the runtime's authoring tools (`list_skills`,
`create_skill`, `update_skill`, `disable_skill`, `enable_skill`) and its bundled
`create-skill` skill. The four writes are marked destructive, so with the tool guard on
each waits for the user's approval.

The run's request comes from `deps.call_context["request"]`, which the default deps
carry. A `deps_factory=` that builds its own deps must put the request there; a run
without it fails rather than quietly running without the caller's skills. An
`agent_factory=` builds its own agent and gets none of this wiring.

## Telling the page

When a run saves a stored skill, the stream carries a `CUSTOM` event once the save
commits:

```json
{"type": "CUSTOM", "name": "ag_ui.skills_changed", "value": {"name": "triage"}}
```

The name is `SKILLS_CHANGED_EVENT_NAME`. The page refetches its catalog on it. The
event names the skill and never carries its body. A save the policy refuses sends
nothing.

The editor's own writes send no event: there is no run stream to carry one, and its
response already is the answer.
