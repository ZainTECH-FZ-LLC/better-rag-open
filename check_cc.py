"""Quick sanity check for Customer Care module imports."""
import sys
sys.path.insert(0, '.')

from src.customer_care.models import CCDocument, CCDocumentChunk, CCDocumentPermission, CCDocumentUserAccess
from src.customer_care.vector_store import CCVectorStore, CCChunkResult
from src.customer_care.retrieval import CCRetrievalPipeline
from src.customer_care.prompts import build_cc_system_prompt
from src.customer_care.change_handler import CCChangeHandler
from src.customer_care.delta_sync import CCDeltaSyncManager
from src.api.routes.customer_care import router as cc_router
print('All CC module imports OK')

from config.settings import get_settings
s = get_settings()
assert hasattr(s, 'CC_SHAREPOINT_DRIVES') and hasattr(s, 'CC_BRAND_GUIDELINES') and hasattr(s, 'CC_UPSELL_PRODUCTS')
assert callable(s.get_cc_sharepoint_drives) and callable(s.get_cc_upsell_products)
print('Settings OK')

prompt = build_cc_system_prompt(
    'Be warm, clear, professional.',
    [{'name': 'Wiyana Pro', 'trigger_keywords': ['upgrade', 'limit'], 'pitch_template': 'Unlock more with Wiyana Pro'}],
    'chat',
)
assert 'answer' in prompt
assert 'Wiyana Pro' in prompt
assert 'CHAT' in prompt
assert 'policy_link' in prompt
assert 'script' in prompt
assert 'upsell' in prompt
print('Prompt builder OK')
print()
print('All checks passed!')
