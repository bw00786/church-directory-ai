// ATEM SDK callback handlers (Windows / COM only).

#include "atem_callbacks.h"

#ifdef _WIN32

#include "atem_controller.h"

// ---------------------------------------------------------------------------
// MixEffectCallback
// ---------------------------------------------------------------------------
MixEffectCallback::MixEffectCallback(AtemController* owner)
    : m_owner(owner), m_ref_count(1) {}

HRESULT STDMETHODCALLTYPE MixEffectCallback::QueryInterface(REFIID iid, LPVOID* ppv) {
    if (ppv == nullptr) {
        return E_POINTER;
    }
    if (iid == IID_IUnknown || iid == __uuidof(IBMDSwitcherMixEffectBlockCallback)) {
        *ppv = static_cast<IBMDSwitcherMixEffectBlockCallback*>(this);
        AddRef();
        return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE MixEffectCallback::AddRef() {
    return ++m_ref_count;
}

ULONG STDMETHODCALLTYPE MixEffectCallback::Release() {
    ULONG count = --m_ref_count;
    if (count == 0) {
        delete this;
    }
    return count;
}

HRESULT STDMETHODCALLTYPE MixEffectCallback::Notify(BMDSwitcherMixEffectBlockEventType eventType) {
    if (m_owner != nullptr) {
        m_owner->OnMixEffectChanged();
    }
    (void)eventType;
    return S_OK;
}

// ---------------------------------------------------------------------------
// SwitcherCallback
// ---------------------------------------------------------------------------
SwitcherCallback::SwitcherCallback(AtemController* owner)
    : m_owner(owner), m_ref_count(1) {}

HRESULT STDMETHODCALLTYPE SwitcherCallback::QueryInterface(REFIID iid, LPVOID* ppv) {
    if (ppv == nullptr) {
        return E_POINTER;
    }
    if (iid == IID_IUnknown || iid == __uuidof(IBMDSwitcherCallback)) {
        *ppv = static_cast<IBMDSwitcherCallback*>(this);
        AddRef();
        return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE SwitcherCallback::AddRef() {
    return ++m_ref_count;
}

ULONG STDMETHODCALLTYPE SwitcherCallback::Release() {
    ULONG count = --m_ref_count;
    if (count == 0) {
        delete this;
    }
    return count;
}

HRESULT STDMETHODCALLTYPE SwitcherCallback::Notify(BMDSwitcherEventType eventType,
                                                   BMDSwitcherVideoMode coreVideoMode) {
    if (m_owner != nullptr && eventType == bmdSwitcherEventTypeDisconnected) {
        m_owner->OnSwitcherDisconnected();
    }
    (void)coreVideoMode;
    return S_OK;
}

// ---------------------------------------------------------------------------
// InputCallback
// ---------------------------------------------------------------------------
InputCallback::InputCallback(AtemController* owner)
    : m_owner(owner), m_ref_count(1) {}

HRESULT STDMETHODCALLTYPE InputCallback::QueryInterface(REFIID iid, LPVOID* ppv) {
    if (ppv == nullptr) {
        return E_POINTER;
    }
    if (iid == IID_IUnknown || iid == __uuidof(IBMDSwitcherInputCallback)) {
        *ppv = static_cast<IBMDSwitcherInputCallback*>(this);
        AddRef();
        return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE InputCallback::AddRef() {
    return ++m_ref_count;
}

ULONG STDMETHODCALLTYPE InputCallback::Release() {
    ULONG count = --m_ref_count;
    if (count == 0) {
        delete this;
    }
    return count;
}

HRESULT STDMETHODCALLTYPE InputCallback::Notify(BMDSwitcherInputEventType eventType) {
    if (m_owner != nullptr) {
        m_owner->OnInputChanged();
    }
    (void)eventType;
    return S_OK;
}

// ---------------------------------------------------------------------------
// StreamCallback
// ---------------------------------------------------------------------------
StreamCallback::StreamCallback(AtemController* owner)
    : m_owner(owner), m_ref_count(1) {}

HRESULT STDMETHODCALLTYPE StreamCallback::QueryInterface(REFIID iid, LPVOID* ppv) {
    if (ppv == nullptr) {
        return E_POINTER;
    }
    if (iid == IID_IUnknown || iid == __uuidof(IBMDSwitcherStreamRTMPCallback)) {
        *ppv = static_cast<IBMDSwitcherStreamRTMPCallback*>(this);
        AddRef();
        return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE StreamCallback::AddRef() {
    return ++m_ref_count;
}

ULONG STDMETHODCALLTYPE StreamCallback::Release() {
    ULONG count = --m_ref_count;
    if (count == 0) {
        delete this;
    }
    return count;
}

HRESULT STDMETHODCALLTYPE StreamCallback::Notify(BMDSwitcherStreamRTMPEventType eventType) {
    if (m_owner != nullptr) {
        m_owner->OnStreamingChanged();
    }
    (void)eventType;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE StreamCallback::NotifyStatus(BMDSwitcherStreamRTMPState stateType,
                                                       BMDSwitcherStreamRTMPError error) {
    if (m_owner != nullptr) {
        m_owner->OnStreamingChanged();
    }
    (void)stateType;
    (void)error;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE StreamCallback::NotifyCloudStreamingDestination(
    BMDSwitcherCloudStreamDestinationConfigurationEventType eventType, unsigned int id) {
    (void)eventType;
    (void)id;
    return S_OK;
}

// ---------------------------------------------------------------------------
// RecordCallback
// ---------------------------------------------------------------------------
RecordCallback::RecordCallback(AtemController* owner)
    : m_owner(owner), m_ref_count(1) {}

HRESULT STDMETHODCALLTYPE RecordCallback::QueryInterface(REFIID iid, LPVOID* ppv) {
    if (ppv == nullptr) {
        return E_POINTER;
    }
    if (iid == IID_IUnknown || iid == __uuidof(IBMDSwitcherRecordAVCallback)) {
        *ppv = static_cast<IBMDSwitcherRecordAVCallback*>(this);
        AddRef();
        return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE RecordCallback::AddRef() {
    return ++m_ref_count;
}

ULONG STDMETHODCALLTYPE RecordCallback::Release() {
    ULONG count = --m_ref_count;
    if (count == 0) {
        delete this;
    }
    return count;
}

HRESULT STDMETHODCALLTYPE RecordCallback::Notify(BMDSwitcherRecordAVEventType eventType) {
    if (m_owner != nullptr) {
        m_owner->OnRecordingChanged();
    }
    (void)eventType;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE RecordCallback::NotifyWorkingSetChange(unsigned int workingSetIndex,
                                                                BMDSwitcherRecordDiskId diskId) {
    (void)workingSetIndex;
    (void)diskId;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE RecordCallback::NotifyDiskAvailability(
    BMDSwitcherRecordDiskAvailabilityEventType eventType, BMDSwitcherRecordDiskId diskId) {
    (void)eventType;
    (void)diskId;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE RecordCallback::NotifyStatus(BMDSwitcherRecordAVState stateType,
                                                       BMDSwitcherRecordAVError error) {
    if (m_owner != nullptr) {
        m_owner->OnRecordingChanged();
    }
    (void)stateType;
    (void)error;
    return S_OK;
}

#endif  // _WIN32
