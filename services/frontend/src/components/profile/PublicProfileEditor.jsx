import { Save, UserCircle2 } from 'lucide-react';
import Button from '../ui/Button';
import ModalShell from '../ui/ModalShell';
import SocialIcon from '../ui/SocialIcon';
import { SOCIAL_LINK_FIELDS } from '../../utils/profileSocial';

export default function PublicProfileEditor({
  open,
  title = 'Edit public profile',
  titleId = 'public-profile-editor-title',
  eyebrow = 'Publisher/Public Profile',
  closeLabel = 'Close public profile editor',
  draft,
  displayNamePreview,
  bannerPreviewUrl,
  logoPreviewUrl,
  onCancel,
  onSubmit,
  onFieldChange,
  onSocialChange,
  onBannerFileChange,
  onLogoFileChange,
  fieldError,
  error,
  saving = false,
  disabled = false,
  saveDisabled = false,
  submitLabel = 'Save Profile',
  savingLabel = 'Saving...',
  cancelLabel = 'Cancel',
  canBackdropClose = true,
  visibilityLabel = 'Make profile public',
  visibilityHelp = 'Public profiles can show banner, logo, bio, contact, and social links.',
  formId = 'public-profile-editor-form',
}) {
  const value = draft || {};
  const getFieldError = typeof fieldError === 'function' ? fieldError : () => '';
  const bannerUrl = bannerPreviewUrl || value.banner_url || '';
  const logoUrl = logoPreviewUrl || value.logo_url || '';

  return (
    <ModalShell
      open={open}
      eyebrow={eyebrow}
      title={title}
      titleId={titleId}
      closeLabel={closeLabel}
      onClose={onCancel}
      canBackdropClose={canBackdropClose}
      closeDisabled={saving}
      footer={(
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
            <span>{cancelLabel}</span>
          </Button>
          <Button type="submit" form={formId} disabled={disabled || saving || saveDisabled}>
            <Save size={15} />
            <span>{saving ? savingLabel : submitLabel}</span>
          </Button>
        </div>
      )}
    >
      <form id={formId} onSubmit={onSubmit}>
        <fieldset disabled={disabled || saving} className="space-y-4 disabled:opacity-60">
          <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={Boolean(value.is_public_profile)}
              onChange={(event) => onFieldChange('is_public_profile', event.target.checked)}
              className="mt-1"
            />
            <span>
              <span className="block font-semibold text-[var(--text-primary)]">{visibilityLabel}</span>
              <span className="mt-1 block text-xs">{visibilityHelp}</span>
            </span>
          </label>

          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem]">
            <div
              className="min-h-28 rounded-xl bg-[var(--surface-container-high)] bg-cover bg-center"
              style={bannerUrl ? {
                backgroundImage: `linear-gradient(90deg, rgba(0,0,0,0.45), rgba(0,0,0,0.18)), url(${bannerUrl})`,
              } : undefined}
            />
            <div className="flex items-center justify-center rounded-xl bg-[var(--surface-container-high)] p-3">
              {logoUrl ? (
                <img
                  src={logoUrl}
                  alt=""
                  className="h-20 w-20 rounded-full object-cover"
                />
              ) : (
                <UserCircle2 size={48} className="text-[var(--text-secondary)]" />
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-sm text-[var(--text-secondary)]">
              Banner image
              <input
                type="file"
                accept="image/*"
                onChange={(event) => onBannerFileChange(event.target.files?.[0] || null)}
                className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
              />
            </label>

            <label className="block text-sm text-[var(--text-secondary)]">
              Logo image
              <input
                type="file"
                accept="image/*"
                onChange={(event) => onLogoFileChange(event.target.files?.[0] || null)}
                className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
              />
            </label>
          </div>

          <label className="block text-sm text-[var(--text-secondary)]">
            Display name
            <input
              type="text"
              value={value.display_name || ''}
              onChange={(event) => onFieldChange('display_name', event.target.value)}
              className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
            />
          </label>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-sm text-[var(--text-secondary)]">
              First name
              <input
                type="text"
                value={value.first_name || ''}
                onChange={(event) => onFieldChange('first_name', event.target.value)}
                className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
              />
            </label>

            <label className="block text-sm text-[var(--text-secondary)]">
              Last name
              <input
                type="text"
                value={value.last_name || ''}
                onChange={(event) => onFieldChange('last_name', event.target.value)}
                className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
              />
            </label>
          </div>

          <label className="block text-sm text-[var(--text-secondary)]">
            Bio
            <textarea
              value={value.bio || ''}
              onChange={(event) => onFieldChange('bio', event.target.value)}
              rows={5}
              className="focus-ring mt-1 w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2 text-sm text-[var(--text-primary)]"
            />
          </label>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-sm text-[var(--text-secondary)]">
              <span className="inline-flex items-center gap-1.5">
                <SocialIcon type="website" size={14} />
                Website
              </span>
              <input
                type="text"
                value={value.website_url || ''}
                onChange={(event) => onFieldChange('website_url', event.target.value)}
                placeholder="example.com"
                className={`focus-ring mt-1 h-10 w-full rounded-xl border bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)] ${
                  getFieldError('website_url') ? 'border-[color:var(--feedback-danger-fg)]' : 'border-[var(--border-subtle)]'
                }`}
              />
              <span className={`mt-1 block text-xs ${getFieldError('website_url') ? 'text-[color:var(--feedback-danger-fg)]' : 'text-[var(--text-secondary)]'}`}>
                {getFieldError('website_url') || 'example.com is accepted and saved as https://example.com'}
              </span>
            </label>

            <label className="block text-sm text-[var(--text-secondary)]">
              <span className="inline-flex items-center gap-1.5">
                <SocialIcon type="contact" size={14} />
                Contact email
              </span>
              <input
                type="text"
                value={value.contact_email || ''}
                onChange={(event) => onFieldChange('contact_email', event.target.value)}
                placeholder="publisher@example.com"
                className={`focus-ring mt-1 h-10 w-full rounded-xl border bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)] ${
                  getFieldError('contact_email') ? 'border-[color:var(--feedback-danger-fg)]' : 'border-[var(--border-subtle)]'
                }`}
              />
              {getFieldError('contact_email') ? (
                <span className="mt-1 block text-xs text-[color:var(--feedback-danger-fg)]">{getFieldError('contact_email')}</span>
              ) : null}
            </label>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-semibold text-[var(--text-primary)]">Social links</p>
            {getFieldError('social_links') ? (
              <p className="text-xs text-[color:var(--feedback-danger-fg)]">{getFieldError('social_links')}</p>
            ) : null}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {SOCIAL_LINK_FIELDS.map((field) => (
                <label key={field.key} className="block text-sm text-[var(--text-secondary)]">
                  <span className="inline-flex items-center gap-1.5">
                    <SocialIcon type={field.key} size={14} />
                    {field.label}
                  </span>
                  <input
                    type="text"
                    value={value.social_links?.[field.key] || ''}
                    onChange={(event) => onSocialChange(field.key, event.target.value)}
                    placeholder={field.placeholder}
                    className={`focus-ring mt-1 h-10 w-full rounded-xl border bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)] ${
                      getFieldError(`social_links.${field.key}`) ? 'border-[color:var(--feedback-danger-fg)]' : 'border-[var(--border-subtle)]'
                    }`}
                  />
                  <span className={`mt-1 block text-xs ${getFieldError(`social_links.${field.key}`) ? 'text-[color:var(--feedback-danger-fg)]' : 'text-[var(--text-secondary)]'}`}>
                    {getFieldError(`social_links.${field.key}`) || field.helper}
                  </span>
                </label>
              ))}
            </div>
          </div>
        </fieldset>
      </form>

      <div className="mt-5 rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
        <p>
          Preview name: <span className="font-semibold text-[var(--text-primary)]">{displayNamePreview}</span>
        </p>
        <p className="mt-1 text-xs">
          Handles and domains are normalized on save.
        </p>
      </div>

      {error ? (
        <p className="mt-3 rounded-xl bg-[var(--status-danger-bg)] px-3 py-2 text-sm text-[var(--status-danger-fg)]">{error}</p>
      ) : null}
    </ModalShell>
  );
}
