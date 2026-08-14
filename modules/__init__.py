"""
Package modules - Regroupe les modules utilitaires pour RecrutBank

Modules disponibles:
- validation: Validation des données (emails, téléphones, honeypot)
- export: Génération de rapports (CSV, Excel, PDF) avec protection CSV Injection
- storage: Gestion des fichiers Supabase avec streaming mémoire
- errors: Gestion des erreurs personnalisées
"""

from .validation import (
    validate_email,
    validate_phone,
    validate_honeypot,
    validate_candidat_form
)

from .export import (
    sanitize_csv_field,
    generate_csv_report
)

from .storage import (
    get_supabase_client,
    upload_file_to_supabase,
    download_file_from_supabase,
    download_file_from_supabase_streaming,
    get_signed_url,
    delete_file_from_supabase,
    list_files_in_bucket
)

from .errors import (
    FileSizeError,
    handle_file_size_limit,
    init_error_handlers,
    validate_file_upload
)

__all__ = [
    # Validation
    'validate_email',
    'validate_phone',
    'validate_honeypot',
    'validate_candidat_form',
    
    # Export
    'sanitize_csv_field',
    'generate_csv_report',
    
    # Storage
    'get_supabase_client',
    'upload_file_to_supabase',
    'download_file_from_supabase',
    'download_file_from_supabase_streaming',
    'get_signed_url',
    'delete_file_from_supabase',
    'list_files_in_bucket',
    
    # Errors
    'FileSizeError',
    'handle_file_size_limit',
    'init_error_handlers',
    'validate_file_upload'
]

__version__ = '1.0.0'
