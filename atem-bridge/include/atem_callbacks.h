// ATEM SDK callback handler classes (Windows / COM only).
//
// Each class implements the relevant Blackmagic callback interface and
// forwards change notifications to the owning AtemController, which then
// refreshes its cached state.

#pragma once

#ifdef _WIN32

#include <windows.h>
#include <atomic>

#include "BMDSwitcherAPI.h"

class AtemController;

// Mix Effect Block: program/preview/transition changes.
class MixEffectCallback : public IBMDSwitcherMixEffectBlockCallback {
public:
    explicit MixEffectCallback(AtemController* owner);

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, LPVOID* ppv) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Notify(BMDSwitcherMixEffectBlockEventType eventType) override;

private:
    virtual ~MixEffectCallback() = default;
    AtemController* m_owner;
    std::atomic<ULONG> m_ref_count;
};

// Switcher: connection / power / video mode changes.
class SwitcherCallback : public IBMDSwitcherCallback {
public:
    explicit SwitcherCallback(AtemController* owner);

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, LPVOID* ppv) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Notify(BMDSwitcherEventType eventType,
                                     BMDSwitcherVideoMode coreVideoMode) override;

private:
    virtual ~SwitcherCallback() = default;
    AtemController* m_owner;
    std::atomic<ULONG> m_ref_count;
};

// Input: name / tally changes.
class InputCallback : public IBMDSwitcherInputCallback {
public:
    explicit InputCallback(AtemController* owner);

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, LPVOID* ppv) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Notify(BMDSwitcherInputEventType eventType) override;

private:
    virtual ~InputCallback() = default;
    AtemController* m_owner;
    std::atomic<ULONG> m_ref_count;
};

// Streaming (RTMP) state changes.
class StreamCallback : public IBMDSwitcherStreamRTMPCallback {
public:
    explicit StreamCallback(AtemController* owner);

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, LPVOID* ppv) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Notify(BMDSwitcherStreamRTMPEventType eventType) override;
    HRESULT STDMETHODCALLTYPE NotifyStatus(BMDSwitcherStreamRTMPState stateType,
                                           BMDSwitcherStreamRTMPError error) override;
    HRESULT STDMETHODCALLTYPE NotifyCloudStreamingDestination(
        BMDSwitcherCloudStreamDestinationConfigurationEventType eventType,
        unsigned int id) override;

private:
    virtual ~StreamCallback() = default;
    AtemController* m_owner;
    std::atomic<ULONG> m_ref_count;
};

// Recording state changes.
class RecordCallback : public IBMDSwitcherRecordAVCallback {
public:
    explicit RecordCallback(AtemController* owner);

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, LPVOID* ppv) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE Notify(BMDSwitcherRecordAVEventType eventType) override;
    HRESULT STDMETHODCALLTYPE NotifyWorkingSetChange(unsigned int workingSetIndex,
                                                     BMDSwitcherRecordDiskId diskId) override;
    HRESULT STDMETHODCALLTYPE NotifyDiskAvailability(
        BMDSwitcherRecordDiskAvailabilityEventType eventType,
        BMDSwitcherRecordDiskId diskId) override;
    HRESULT STDMETHODCALLTYPE NotifyStatus(BMDSwitcherRecordAVState stateType,
                                           BMDSwitcherRecordAVError error) override;

private:
    virtual ~RecordCallback() = default;
    AtemController* m_owner;
    std::atomic<ULONG> m_ref_count;
};

#endif  // _WIN32
